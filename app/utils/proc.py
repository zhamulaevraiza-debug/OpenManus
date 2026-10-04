"""Helpers for running agent-controlled subprocesses safely.

Every process that executes model-written code (python_execute, bash, chart rendering,
shell helpers of the file tools) is started through these helpers so that it:

* runs inside the run's workspace with a minimal environment that never contains
  server secrets (API keys, tokens, cloud credentials, OPENMANUS_* settings);
* gets its own session / process group, so the whole tree can be killed at once;
* drops privileges to ``OPENMANUS_EXEC_USER`` when the server runs as root.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from app.context import get_workspace


EXEC_USER_ENV = "OPENMANUS_EXEC_USER"
EXEC_ENV_ALLOWLIST_ENV = "OPENMANUS_EXEC_ENV_ALLOWLIST"

DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Variables copied from the server environment when present.
_PASSTHROUGH_VARS = (
    "TZ",
    "TMPDIR",
    "PLAYWRIGHT_BROWSERS_PATH",
    "TIKTOKEN_CACHE_DIR",
    "NODE_PATH",
)

# Never forwarded, even when explicitly allow-listed.
_DENIED_PATTERNS = (
    "OPENMANUS_*",
    "AWS_*",
    "DAYTONA_*",
    "AZURE_*",
    "GOOGLE_*",
    "*_API_KEY",
    "*_APIKEY",
    "*_KEY",
    "*_TOKEN",
    "*_SECRET",
    "*_SECRET_*",
    "*PASSWORD*",
    "*_CREDENTIALS",
)

_TERMINATE_GRACE_SECONDS = 2.0


def _is_denied(name: str) -> bool:
    upper = name.upper()
    return any(fnmatch.fnmatchcase(upper, pattern) for pattern in _DENIED_PATTERNS)


def exec_user_ids() -> Optional[Tuple[int, int]]:
    """Return ``(uid, gid)`` of ``OPENMANUS_EXEC_USER`` when privileges can be dropped.

    Dropping privileges is only possible (and only attempted) when the server runs as
    root and the configured user exists; otherwise agent code runs as the server user.
    """
    name = os.environ.get(EXEC_USER_ENV, "").strip()
    if not name or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    try:
        import pwd

        entry = pwd.getpwnam(name)
    except (ImportError, KeyError):
        return None
    return entry.pw_uid, entry.pw_gid


def scrubbed_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a minimal environment for agent subprocesses.

    Contains PATH, locale, HOME (the current workspace), Python/matplotlib defaults and
    a few tool caches, plus variables named in ``OPENMANUS_EXEC_ENV_ALLOWLIST``
    (comma separated) unless they look like secrets. ``extra`` is applied last and is
    trusted (it comes from code, not from the environment).
    """
    source = os.environ
    env: Dict[str, str] = {
        "PATH": source.get("PATH") or DEFAULT_PATH,
        "LANG": source.get("LANG") or "C.UTF-8",
        "LC_ALL": source.get("LC_ALL") or source.get("LANG") or "C.UTF-8",
        "HOME": str(get_workspace()),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
    }
    for name in _PASSTHROUGH_VARS:
        value = source.get(name)
        if value:
            env[name] = value

    for name in source.get(EXEC_ENV_ALLOWLIST_ENV, "").split(","):
        name = name.strip()
        if name and name in source and not _is_denied(name):
            env[name] = source[name]

    if extra:
        env.update({key: str(value) for key, value in extra.items()})
    return env


def subprocess_kwargs(
    cwd: Optional[Union[str, Path]] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> dict:
    """Keyword arguments for ``asyncio.create_subprocess_exec`` / ``_shell``.

    The process runs in ``cwd`` (default: the current workspace) with a scrubbed
    environment whose HOME is that directory, in a new session (own process group),
    and as ``OPENMANUS_EXEC_USER`` when the server runs as root.
    """
    workdir = Path(cwd) if cwd is not None else get_workspace()
    env_extra = {"HOME": str(workdir)}
    if extra_env:
        env_extra.update(extra_env)
    kwargs = {
        "cwd": str(workdir),
        "env": scrubbed_env(env_extra),
        "start_new_session": True,
    }
    ids = exec_user_ids()
    if ids is not None:
        uid, gid = ids
        kwargs.update(user=uid, group=gid, extra_groups=[])
    return kwargs


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    """Send ``sig`` to the process group led by ``proc`` (best effort)."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        if proc.returncode is None:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass


def kill_process_group_now(proc: asyncio.subprocess.Process) -> None:
    """Synchronously SIGKILL the whole process group (safe inside cancel handlers)."""
    _signal_group(proc, signal.SIGKILL)


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate ``proc`` and every process in its group, then reap it.

    Sends SIGTERM to the group, waits up to two seconds for the leader to exit and
    then SIGKILLs the group (background children may outlive the leader). The kill
    is delivered even if this coroutine itself gets cancelled while waiting.
    """
    if proc.returncode is None:
        _signal_group(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), _TERMINATE_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass
    finally:
        _signal_group(proc, signal.SIGKILL)
    await proc.wait()


class CappedBuffer:
    """Byte buffer that keeps the head and the tail of a stream within a size limit."""

    def __init__(self, limit: int):
        self.limit = max(limit, 64)
        self._head_limit = self.limit * 2 // 3
        self._tail_limit = self.limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> None:
        room = self._head_limit - len(self._head)
        if room > 0:
            self._head += data[:room]
            data = data[room:]
        if data:
            self._tail += data
            excess = len(self._tail) - self._tail_limit
            if excess > 0:
                del self._tail[:excess]
                self.dropped += excess

    def text(self) -> str:
        """Decoded content with a marker where data was dropped."""
        head = self._head.decode("utf-8", errors="replace")
        if not self.dropped:
            return head + self._tail.decode("utf-8", errors="replace")
        tail = self._tail.decode("utf-8", errors="replace")
        return f"{head}\n... [{self.dropped} bytes of output truncated] ...\n{tail}"


@dataclass
class ProcessResult:
    """Outcome of :func:`run_process`."""

    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False


async def _pump(stream: Optional[asyncio.StreamReader], sink: CappedBuffer) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        sink.feed(chunk)


async def _feed_stdin(proc: asyncio.subprocess.Process, data: Optional[bytes]) -> None:
    if proc.stdin is None:
        return
    try:
        if data:
            proc.stdin.write(data)
            await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def run_process(
    *args: str,
    stdin_data: Optional[bytes] = None,
    timeout: Optional[float] = None,
    output_limit: int = 20000,
    merge_stderr: bool = False,
    shell: bool = False,
    cwd: Optional[Union[str, Path]] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> ProcessResult:
    """Run a sandboxed subprocess to completion with bounded output.

    Output is drained continuously (the child never blocks on a full pipe) and capped
    at ``output_limit`` bytes per stream. On timeout or cancellation the whole process
    group is killed before returning / re-raising.
    """
    kwargs = subprocess_kwargs(cwd=cwd, extra_env=extra_env)
    pipes = dict(
        stdin=asyncio.subprocess.PIPE
        if stdin_data is not None
        else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
    )
    if shell:
        if len(args) != 1:
            raise ValueError("shell=True expects a single command string")
        proc = await asyncio.create_subprocess_shell(args[0], **pipes, **kwargs)
    else:
        proc = await asyncio.create_subprocess_exec(*args, **pipes, **kwargs)

    out, err = CappedBuffer(output_limit), CappedBuffer(output_limit)
    io_tasks = [
        asyncio.ensure_future(_pump(proc.stdout, out)),
        asyncio.ensure_future(_pump(proc.stderr, err)),
        asyncio.ensure_future(_feed_stdin(proc, stdin_data)),
    ]
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await kill_process_tree(proc)
        else:
            # The leader exited; reap any leftovers that still hold the pipes open.
            kill_process_group_now(proc)
        await asyncio.gather(*io_tasks, return_exceptions=True)
    except BaseException:
        kill_process_group_now(proc)
        for task in io_tasks:
            task.cancel()
        raise

    return ProcessResult(
        returncode=proc.returncode,
        stdout=out.text(),
        stderr=err.text(),
        timed_out=timed_out,
    )
