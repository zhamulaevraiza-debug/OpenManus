"""
Asynchronous Docker Terminal

This module provides asynchronous terminal functionality for Docker containers,
allowing interactive command execution with timeout control.
"""

import asyncio
import re
import secrets
import socket
from typing import Dict, Optional, Tuple, Union

import docker
from docker import APIClient
from docker.errors import APIError
from docker.models.containers import Container

from app.logger import logger


PROMPT = b"$ "
STARTUP_TIMEOUT_SECONDS = 30
INTERRUPT_TIMEOUT_SECONDS = 5
_EXIT_CODE_MARKER = "__OPENMANUS_RC__"
_EXIT_CODE_LINE = re.compile(rb"^" + _EXIT_CODE_MARKER.encode() + rb"(\d+)$")


class DockerSession:
    def __init__(self, container_id: str, api: Optional[APIClient] = None) -> None:
        """Initializes a Docker session.

        Args:
            container_id: ID of the Docker container.
            api: Low-level Docker API client (default: from the environment, so
                DOCKER_HOST and friends are honoured).
        """
        self.api = api or docker.from_env().api
        self.container_id = container_id
        self.exec_id = None
        self.socket = None
        self._lock = asyncio.Lock()

    async def create(self, working_dir: str, env_vars: Dict[str, str]) -> None:
        """Creates an interactive session with the container.

        Args:
            working_dir: Working directory inside the container.
            env_vars: Environment variables to set.

        Raises:
            RuntimeError: If socket connection fails or the shell does not start.
        """
        startup_command = [
            "bash",
            "-c",
            f"cd {working_dir} && "
            "PROMPT_COMMAND='' "
            "PS1='$ ' "
            "exec bash --norc --noprofile",
        ]

        exec_data = await asyncio.to_thread(
            self.api.exec_create,
            self.container_id,
            startup_command,
            stdin=True,
            tty=True,
            stdout=True,
            stderr=True,
            privileged=True,
            user="root",
            environment={**env_vars, "TERM": "dumb", "PS1": "$ ", "PROMPT_COMMAND": ""},
        )
        self.exec_id = exec_data["Id"]

        socket_data = await asyncio.to_thread(
            self.api.exec_start,
            self.exec_id,
            socket=True,
            tty=True,
            stream=True,
            demux=True,
        )

        if hasattr(socket_data, "_sock"):
            self.socket = socket_data._sock
            self.socket.setblocking(False)
        else:
            raise RuntimeError("Failed to get socket connection")

        try:
            await asyncio.wait_for(self._read_until_prompt(), STARTUP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError("Timed out waiting for the container shell") from None

    async def close(self) -> None:
        """Cleans up session resources.

        1. Sends exit command
        2. Closes socket connection
        3. Checks and cleans up exec instance
        """
        try:
            if self.socket:
                # Send exit command to close bash session
                try:
                    self.socket.sendall(b"exit\n")
                    # Allow time for command execution
                    await asyncio.sleep(0.1)
                except OSError:
                    pass  # Ignore sending errors, continue cleanup

                # Close socket connection
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Some platforms may not support shutdown

                self.socket.close()
                self.socket = None

            if self.exec_id:
                try:
                    # Check exec instance status
                    exec_inspect = await asyncio.to_thread(
                        self.api.exec_inspect, self.exec_id
                    )
                    if exec_inspect.get("Running", False):
                        # If still running, wait for it to complete
                        await asyncio.sleep(0.5)
                except Exception:
                    pass  # Ignore inspection errors, continue cleanup

                self.exec_id = None

        except Exception as e:
            # Log error but don't raise, ensure cleanup continues
            logger.warning(f"Error during session cleanup: {e}")

    async def _recv(self) -> bytes:
        """Receive a chunk from the non-blocking socket; raises on EOF."""
        while True:
            try:
                chunk = self.socket.recv(4096)
            except BlockingIOError:
                await asyncio.sleep(0.05)
                continue
            if not chunk:
                raise ConnectionError("Container shell closed the connection")
            return chunk

    async def _read_until_prompt(self) -> str:
        """Reads output until prompt is found.

        Returns:
            String containing output up to the prompt.

        Raises:
            ConnectionError: If the shell connection reaches EOF.
        """
        buffer = b""
        while PROMPT not in buffer:
            buffer += await self._recv()
        return buffer.decode("utf-8", errors="replace")

    async def _interrupt(self) -> None:
        """Interrupt the running command (Ctrl-C) and resynchronise the stream.

        A unique token is echoed after the interrupt and everything up to it (and
        the following prompt) is discarded, so later commands read clean output.
        """
        token = f"__OPENMANUS_SYNC_{secrets.token_hex(4)}__".encode()
        try:
            self.socket.sendall(b"\x03")
            await asyncio.sleep(0.1)
            self.socket.sendall(b"echo " + token + b"\n")
            await asyncio.wait_for(self._drain_until(token), INTERRUPT_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning(f"Could not interrupt the timed out command: {e}")

    async def _drain_until(self, token: bytes) -> None:
        buffer = b""
        seen = False
        while True:
            buffer += await self._recv()
            lines = buffer.split(b"\n")
            buffer = lines[-1]
            seen = seen or any(line.strip() == token for line in lines[:-1])
            if seen and buffer.endswith(PROMPT):
                return

    async def execute(self, command: str, timeout: Optional[int] = None) -> str:
        """Executes a command and returns cleaned output.

        Args:
            command: Shell command to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            Command output as string with prompt markers removed.

        Raises:
            RuntimeError: If session not initialized or execution fails.
            TimeoutError: If command execution exceeds timeout.
        """
        if not self.socket:
            raise RuntimeError("Session not initialized")

        async with self._lock:
            # Sanitize command to prevent shell injection
            sanitized_command = self._sanitize_command(command)
            full_command = f"{sanitized_command}\necho {_EXIT_CODE_MARKER}$?\n"
            try:
                self.socket.sendall(full_command.encode())
                if timeout:
                    result = await asyncio.wait_for(self._read_output(), timeout)
                else:
                    result = await self._read_output()
            except asyncio.TimeoutError:
                await self._interrupt()
                raise TimeoutError(
                    f"Command execution timed out after {timeout} seconds"
                ) from None
            except Exception as e:
                raise RuntimeError(f"Failed to execute command: {e}") from e

            return result.strip()

    async def _read_output(self) -> str:
        """Collect the output of one command up to the prompt after its exit code.

        The exit code is echoed with a marker (so numeric output lines are kept);
        the shell's echo of the typed command lines is dropped.
        """
        buffer = b""
        result_lines = []
        command_echo_skipped = False
        exit_code_seen = False
        echo_line = f"echo {_EXIT_CODE_MARKER}$?".encode()

        while True:
            buffer += await self._recv()
            lines = buffer.split(b"\n")
            buffer = lines[-1]

            for line in lines[:-1]:
                line = line.rstrip(b"\r")

                if not command_echo_skipped:
                    command_echo_skipped = True
                    continue

                stripped = line.strip()
                if _EXIT_CODE_LINE.match(stripped):
                    exit_code_seen = True
                    continue
                if stripped.endswith(echo_line):
                    # Output without a trailing newline shares the line with the
                    # prompt and the echoed exit-code command.
                    line = line[: line.rfind(echo_line)]
                    if line.endswith(PROMPT):
                        line = line[: -len(PROMPT)]
                    stripped = line.strip()

                if stripped:
                    result_lines.append(line)

            if exit_code_seen and buffer.endswith(PROMPT):
                break

        return b"\n".join(result_lines).decode("utf-8", errors="replace")

    def _sanitize_command(self, command: str) -> str:
        """Sanitizes the command string to prevent shell injection.

        Args:
            command: Raw command string.

        Returns:
            Sanitized command string.

        Raises:
            ValueError: If command contains potentially dangerous patterns.
        """

        # Additional checks for specific risky commands
        risky_commands = [
            "rm -rf /",
            "rm -rf /*",
            "mkfs",
            "dd if=/dev/zero",
            ":(){:|:&};:",
            "chmod -R 777 /",
            "chown -R",
        ]

        for risky in risky_commands:
            if risky in command.lower():
                raise ValueError(
                    f"Command contains potentially dangerous operation: {risky}"
                )

        return command


class AsyncDockerizedTerminal:
    def __init__(
        self,
        container: Union[str, Container],
        working_dir: str = "/workspace",
        env_vars: Optional[Dict[str, str]] = None,
        default_timeout: int = 60,
        client: Optional[docker.DockerClient] = None,
    ) -> None:
        """Initializes an asynchronous terminal for Docker containers.

        Args:
            container: Docker container ID or Container object.
            working_dir: Working directory inside the container.
            env_vars: Environment variables to set.
            default_timeout: Default command execution timeout in seconds.
            client: Docker client to reuse (default: from the environment).
        """
        self.client = client or docker.from_env()
        self.container = (
            container
            if isinstance(container, Container)
            else self.client.containers.get(container)
        )
        self.working_dir = working_dir
        self.env_vars = env_vars or {}
        self.default_timeout = default_timeout
        self.session = None

    async def init(self) -> None:
        """Initializes the terminal environment.

        Ensures working directory exists and creates an interactive session.

        Raises:
            RuntimeError: If initialization fails.
        """
        await self._ensure_workdir()

        self.session = DockerSession(self.container.id, api=self.client.api)
        await self.session.create(self.working_dir, self.env_vars)

    async def _ensure_workdir(self) -> None:
        """Ensures working directory exists in container.

        Raises:
            RuntimeError: If directory creation fails.
        """
        try:
            await self._exec_simple(f"mkdir -p {self.working_dir}")
        except APIError as e:
            raise RuntimeError(f"Failed to create working directory: {e}")

    async def _exec_simple(self, cmd: str) -> Tuple[int, str]:
        """Executes a simple command using Docker's exec_run.

        Args:
            cmd: Command to execute.

        Returns:
            Tuple of (exit_code, output).
        """
        result = await asyncio.to_thread(
            self.container.exec_run, cmd, environment=self.env_vars
        )
        return result.exit_code, result.output.decode("utf-8")

    async def run_command(self, cmd: str, timeout: Optional[int] = None) -> str:
        """Runs a command in the container with timeout.

        Args:
            cmd: Shell command to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            Command output as string.

        Raises:
            RuntimeError: If terminal not initialized.
        """
        if not self.session:
            raise RuntimeError("Terminal not initialized")

        return await self.session.execute(cmd, timeout=timeout or self.default_timeout)

    async def close(self) -> None:
        """Closes the terminal session."""
        if self.session:
            await self.session.close()

    async def __aenter__(self) -> "AsyncDockerizedTerminal":
        """Async context manager entry."""
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
