"""File operation interfaces and implementations for local and sandbox environments."""

import asyncio
import os
import shlex
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    Protocol,
    Tuple,
    Union,
    runtime_checkable,
)

from app.config import SandboxSettings, config
from app.context import get_workspace
from app.exceptions import ToolError
from app.utils.proc import exec_user_ids, run_process


if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.sandbox.client import BaseSandboxClient


PathLike = Union[str, Path]


@runtime_checkable
class FileOperator(Protocol):
    """Interface for file operations in different environments."""

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file."""
        ...

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file."""
        ...

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        ...

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        ...

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command and return (return_code, stdout, stderr)."""
        ...


def _write_text_owned(path: Path, content: str, encoding: str) -> None:
    """Write ``path`` creating parent directories.

    Newly created files and directories are handed to the exec user (when the server
    runs as root with ``OPENMANUS_EXEC_USER``) so agent code can modify them later.
    """
    missing: List[Path] = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)

    ids = exec_user_ids()
    if ids is None:
        return
    created = list(reversed(missing)) + ([path] if is_new else [])
    for item in created:
        try:
            os.chown(item, *ids, follow_symlinks=False)
        except OSError:
            pass


class LocalFileOperator(FileOperator):
    """File operations implementation for local filesystem."""

    encoding: str = "utf-8"

    async def read_file(self, path: PathLike) -> str:
        """Read content from a local file."""
        try:
            return await asyncio.to_thread(Path(path).read_text, encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to read {path}: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a local file (parent directories are created)."""
        try:
            await asyncio.to_thread(
                _write_text_owned, Path(path), content, self.encoding
            )
        except Exception as e:
            raise ToolError(f"Failed to write to {path}: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory."""
        return Path(path).is_dir()

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists."""
        return Path(path).exists()

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a shell command in the workspace with a scrubbed environment."""
        result = await run_process(
            cmd, shell=True, timeout=timeout, cwd=get_workspace()
        )
        if result.timed_out:
            raise TimeoutError(f"Command '{cmd}' timed out after {timeout} seconds")
        return result.returncode or 0, result.stdout, result.stderr


class SandboxFileOperator(FileOperator):
    """File operations implementation for the Docker sandbox environment.

    Each operator owns its sandbox client (created lazily on first use) unless one is
    injected; call :meth:`cleanup` to remove the container.
    """

    def __init__(self, sandbox_client: Optional["BaseSandboxClient"] = None):
        if sandbox_client is None:
            from app.sandbox.client import create_sandbox_client

            sandbox_client = create_sandbox_client()
        self.sandbox_client = sandbox_client
        self._init_lock = asyncio.Lock()

    async def _ensure_sandbox_initialized(self):
        """Ensure sandbox is initialized."""
        async with self._init_lock:
            if not self.sandbox_client.sandbox:
                await self.sandbox_client.create(
                    config=config.sandbox or SandboxSettings()
                )

    async def read_file(self, path: PathLike) -> str:
        """Read content from a file in sandbox."""
        await self._ensure_sandbox_initialized()
        try:
            return await self.sandbox_client.read_file(str(path))
        except Exception as e:
            raise ToolError(f"Failed to read {path} in sandbox: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """Write content to a file in sandbox."""
        await self._ensure_sandbox_initialized()
        try:
            await self.sandbox_client.write_file(str(path), content)
        except Exception as e:
            raise ToolError(f"Failed to write to {path} in sandbox: {str(e)}") from None

    async def _test(self, flag: str, path: PathLike) -> bool:
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test {flag} {shlex.quote(str(path))} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def is_directory(self, path: PathLike) -> bool:
        """Check if path points to a directory in sandbox."""
        return await self._test("-d", path)

    async def exists(self, path: PathLike) -> bool:
        """Check if path exists in sandbox."""
        return await self._test("-e", path)

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """Run a command in sandbox environment."""
        await self._ensure_sandbox_initialized()
        try:
            stdout = await self.sandbox_client.run_command(
                cmd, timeout=int(timeout) if timeout else None
            )
            return (
                0,  # Always return 0 since we don't have explicit return code from sandbox
                stdout,
                "",  # No stderr capture in the current sandbox implementation
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds in sandbox"
            ) from exc
        except Exception as exc:
            return 1, "", f"Error executing command in sandbox: {str(exc)}"

    async def cleanup(self) -> None:
        """Remove the sandbox container owned by this operator."""
        await self.sandbox_client.cleanup()
