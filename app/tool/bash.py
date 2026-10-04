import asyncio
import re
import secrets
import shlex
from pathlib import Path
from typing import Optional, Pattern, Tuple

from pydantic import Field, PrivateAttr

from app.context import get_workspace
from app.exceptions import ToolError
from app.tool.base import BaseTool, CLIResult
from app.utils.proc import (
    CappedBuffer,
    kill_process_group_now,
    kill_process_tree,
    subprocess_kwargs,
)


DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_OUTPUT_BYTES = 20000

_BASH_DESCRIPTION = """Execute a bash command in a persistent shell session.
* The shell starts in the workspace directory; `cd`, exported variables and functions persist between calls.
* Long running commands: run them in the background and redirect the output to a file, e.g. command = `python3 app.py > server.log 2>&1 &`.
* Timeout: a command that runs longer than the time limit is killed together with the shell, and the shell is restarted (working directory and variables are reset). Retry such commands in the background.
* Commands do not read from STDIN; use non-interactive flags (e.g. `apt-get -y`, `pip install -q`).
* An empty `command` returns output produced by background processes since the last call; `ctrl+c` stops all running processes and restarts the shell.
"""

# Bytes held back from the output while waiting for the sentinel, so a partially
# received sentinel never leaks into the command output.
_SENTINEL_HOLDBACK = 128


class _ShellExited(Exception):
    """The shell process terminated while a command was running."""


class _StreamCollector:
    """Continuously drains one pipe of the shell into a bounded buffer.

    While a command is running the collector watches for that command's sentinel
    line; everything before it is the command output, anything after it is kept for
    the next call (e.g. output of background jobs).
    """

    def __init__(self, stream: asyncio.StreamReader, limit: int):
        self._limit = limit
        self._buffer = CappedBuffer(limit)
        self._pending = bytearray()
        self._marker: Optional[Pattern[bytes]] = None
        self._result: Optional[Tuple[str, int]] = None
        self._eof = False
        self._changed = asyncio.Event()
        self._task = asyncio.ensure_future(self._drain(stream))

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                self._feed(chunk)
                self._changed.set()
        finally:
            self._eof = True
            self._changed.set()

    def _feed(self, chunk: bytes) -> None:
        self._pending += chunk
        if self._marker is not None:
            match = self._marker.search(self._pending)
            if match:
                self._buffer.feed(bytes(self._pending[: match.start()]))
                self._result = (self._buffer.text(), int(match.group(1)))
                self._buffer = CappedBuffer(self._limit)
                del self._pending[: match.end()]
                self._marker = None
        hold = _SENTINEL_HOLDBACK if self._marker is not None else 0
        if len(self._pending) > hold:
            cut = len(self._pending) - hold
            self._buffer.feed(bytes(self._pending[:cut]))
            del self._pending[:cut]

    def arm(self, marker: Pattern[bytes]) -> None:
        """Start watching for ``marker`` (with the exit code as group 1)."""
        self._marker = marker
        self._result = None

    async def wait_result(self) -> Tuple[str, int]:
        """Wait for the armed sentinel; returns ``(output, exit_code)``."""
        while True:
            if self._result is not None:
                result, self._result = self._result, None
                return result
            if self._eof:
                raise _ShellExited()
            self._changed.clear()
            await self._changed.wait()

    def partial_output(self) -> str:
        """Output collected so far for the running command."""
        self._buffer.feed(bytes(self._pending))
        self._pending.clear()
        return self._buffer.text()

    async def close(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


class _BashSession:
    """A bash process plus the protocol used to run commands in it."""

    def __init__(self, cwd: Path, timeout: float):
        self._cwd = cwd
        self._timeout = timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._stdout: Optional[_StreamCollector] = None
        self._stderr: Optional[_StreamCollector] = None
        self._counter = 0
        self._token = secrets.token_hex(8)
        self._killed = False

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "--noprofile",
            "--norc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **subprocess_kwargs(cwd=self._cwd),
        )
        self._stdout = _StreamCollector(self._process.stdout, MAX_OUTPUT_BYTES)
        self._stderr = _StreamCollector(self._process.stderr, MAX_OUTPUT_BYTES)

    @property
    def alive(self) -> bool:
        return (
            not self._killed
            and self._process is not None
            and self._process.returncode is None
        )

    def _script(self, command: str, token: str) -> bytes:
        sentinel = f"printf '\\n<<exit:{token}:%s>>\\n' \"$__om_rc\""
        return (
            f"__om_cmd={shlex.quote(command)}\n"
            'eval "$__om_cmd" < /dev/null\n'
            f"__om_rc=$?; {sentinel}; {sentinel} >&2\n"
        ).encode("utf-8", errors="surrogateescape")

    async def run(self, command: str) -> CLIResult:
        """Run ``command`` and wait for its completion (bounded by the timeout)."""
        assert self._process and self._stdout and self._stderr
        self._counter += 1
        token = f"{self._token}{self._counter}"
        marker = re.compile(rb"\n<<exit:" + token.encode() + rb":(\d+)>>\n")
        self._stdout.arm(marker)
        self._stderr.arm(marker)

        try:
            self._process.stdin.write(self._script(command.replace("\0", ""), token))
            await self._process.stdin.drain()
            (stdout, code), (stderr, _) = await asyncio.wait_for(
                asyncio.gather(self._stdout.wait_result(), self._stderr.wait_result()),
                self._timeout,
            )
        except asyncio.TimeoutError:
            partial = self._partial_output()
            await self.close()
            message = (
                f"Command timed out after {self._timeout:g} seconds and was killed; "
                "the shell has been restarted (working directory and variables were "
                "reset). Run long commands in the background, e.g. "
                "`cmd > out.log 2>&1 &`."
            )
            return CLIResult(error=f"{message}\n{partial}" if partial else message)
        except (_ShellExited, BrokenPipeError, ConnectionResetError):
            partial = self._partial_output()
            await self.close()
            message = (
                f"The shell exited (code {self._process.returncode}); "
                "it will be restarted on the next command."
            )
            return CLIResult(error=f"{partial}\n{message}" if partial else message)
        except asyncio.CancelledError:
            self.kill_now()
            raise

        return CLIResult(output=_format_output(stdout, stderr, code))

    def _partial_output(self) -> str:
        parts = [
            collector.partial_output()
            for collector in (self._stdout, self._stderr)
            if collector is not None
        ]
        return "\n".join(part for part in parts if part).strip("\n")

    def kill_now(self) -> None:
        """Synchronously kill the shell and everything it started."""
        self._killed = True
        if self._process is not None:
            kill_process_group_now(self._process)

    async def close(self) -> None:
        """Kill the shell's process group and stop the output readers."""
        self._killed = True
        if self._process is not None:
            try:
                await kill_process_tree(self._process)
            finally:
                for collector in (self._stdout, self._stderr):
                    if collector is not None:
                        await collector.close()


def _format_output(stdout: str, stderr: str, code: int) -> str:
    stdout = stdout[:-1] if stdout.endswith("\n") else stdout
    stderr = stderr[:-1] if stderr.endswith("\n") else stderr
    parts = [stdout] if stdout else []
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if code != 0:
        parts.append(f"[exit code: {code}]")
    return "\n".join(parts)


class Bash(BaseTool):
    """A tool for executing bash commands"""

    name: str = "bash"
    description: str = _BASH_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute. Can be empty to view output of background processes. Can be `ctrl+c` to stop all running processes.",
            },
            "restart": {
                "type": "boolean",
                "description": "Set to true to restart the shell session (kills running processes).",
            },
        },
        "required": ["command"],
    }
    timeout: float = Field(default=DEFAULT_TIMEOUT_SECONDS, exclude=True)

    _session: Optional[_BashSession] = PrivateAttr(default=None)
    _lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    async def execute(
        self, command: str | None = None, restart: bool = False, **kwargs
    ) -> CLIResult:
        async with self._lock:
            if restart:
                await self._restart()
                return CLIResult(system="tool has been restarted.")

            if command is None:
                raise ToolError("no command provided.")

            if command.strip().lower() == "ctrl+c":
                await self._restart()
                return CLIResult(
                    output="Stopped all running processes; the shell has been restarted."
                )

            if self._session is None or not self._session.alive:
                await self._restart()
            return await self._session.run(command)

    async def _restart(self) -> None:
        await self._stop()
        session = _BashSession(get_workspace(), self.timeout)
        await session.start()
        self._session = session

    async def _stop(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def cleanup(self) -> None:
        """Kill the shell and all processes it started."""
        await self._stop()
