import asyncio
import re
import secrets
import shlex
from typing import Any, Dict, Optional, TypeVar
from uuid import uuid4

from pydantic import PrivateAttr

from app.daytona.tool_base import Sandbox, SandboxToolsBase
from app.tool.base import ToolResult
from app.utils.logger import logger


MAX_OUTPUT_CHARS = 20000
POLL_INTERVAL_SECONDS = 2
RAW_COMMAND_TIMEOUT_SECONDS = 30
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


Context = TypeVar("Context")
_SHELL_DESCRIPTION = """\
Execute a shell command in the workspace directory.
IMPORTANT: Commands are non-blocking by default and run in a tmux session.
This is ideal for long-running operations like starting servers or build processes.
Uses sessions to maintain state between commands.
This tool is essential for running CLI tools, installing packages, and managing system operations.
"""


class SandboxShellTool(SandboxToolsBase):
    """Tool for executing tasks in a Daytona sandbox with browser-use capabilities.
    Uses sessions for maintaining state between commands and provides comprehensive process management.
    """

    name: str = "sandbox_shell"
    description: str = _SHELL_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "execute_command",
                    "check_command_output",
                    "terminate_command",
                    "list_commands",
                ],
                "description": "The shell action to perform",
            },
            "command": {
                "type": "string",
                "description": "The shell command to execute. Use this for running CLI tools, installing packages, "
                "or system operations. Commands can be chained using &&, ||, and | operators.",
            },
            "folder": {
                "type": "string",
                "description": "Optional relative path to a subdirectory of /workspace where the command should be "
                "executed. Example: 'data/pdfs'",
            },
            "session_name": {
                "type": "string",
                "description": "Optional name of the tmux session to use. Use named sessions for related commands "
                "that need to maintain state. Defaults to a random session name.",
            },
            "blocking": {
                "type": "boolean",
                "description": "Whether to wait for the command to complete. Defaults to false for non-blocking "
                "execution.",
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds for blocking commands. Defaults to 60. Ignored for "
                "non-blocking commands.",
                "default": 60,
            },
            "kill_session": {
                "type": "boolean",
                "description": "Whether to terminate the tmux session after checking. Set to true when you're done "
                "with the command.",
                "default": False,
            },
        },
        "required": ["action"],
        "dependencies": {
            "execute_command": ["command"],
            "check_command_output": ["session_name"],
            "terminate_command": ["session_name"],
            "list_commands": [],
        },
    }

    _lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    def __init__(
        self, sandbox: Optional[Sandbox] = None, thread_id: Optional[str] = None, **data
    ):
        """Initialize with optional sandbox and thread_id."""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    async def _ensure_session(self, session_name: str = "default") -> str:
        """Ensure a session exists and return its ID."""
        if session_name not in self._sessions:
            session_id = str(uuid4())
            try:
                await self._ensure_sandbox()  # Ensure sandbox is initialized
                await asyncio.to_thread(self.sandbox.process.create_session, session_id)
                self._sessions[session_name] = session_id
            except Exception as e:
                raise RuntimeError(f"Failed to create session: {str(e)}")
        return self._sessions[session_name]

    async def _cleanup_session(self, session_name: str):
        """Clean up a session if it exists."""
        if session_name in self._sessions:
            try:
                await self._ensure_sandbox()  # Ensure sandbox is initialized
                await asyncio.to_thread(
                    self.sandbox.process.delete_session, self._sessions[session_name]
                )
                del self._sessions[session_name]
            except Exception as e:
                logger.warning(f"Failed to cleanup session {session_name}: {str(e)}")

    async def _execute_raw_command(self, command: str) -> Dict[str, Any]:
        """Execute a raw command directly in the sandbox."""
        from daytona import SessionExecuteRequest

        # Ensure session exists for raw commands
        session_id = await self._ensure_session("raw_commands")
        req = SessionExecuteRequest(command=command, run_async=False)

        def _run() -> Dict[str, Any]:
            response = self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=req,
                timeout=RAW_COMMAND_TIMEOUT_SECONDS,
            )
            logs = self.sandbox.process.get_session_command_logs(
                session_id=session_id, command_id=response.cmd_id
            )
            return {"output": logs, "exit_code": response.exit_code}

        return await asyncio.to_thread(_run)

    async def _tmux(self, *args: str) -> Dict[str, Any]:
        """Run a tmux command with safely quoted arguments."""
        return await self._execute_raw_command(
            "tmux " + " ".join(shlex.quote(arg) for arg in args)
        )

    async def _session_exists(self, session_name: str) -> bool:
        result = await self._execute_raw_command(
            f"tmux has-session -t {shlex.quote(session_name)} 2>/dev/null "
            "|| echo 'not_exists'"
        )
        return "not_exists" not in result.get("output", "")

    async def _capture_pane(self, session_name: str) -> str:
        result = await self._tmux(
            "capture-pane", "-t", session_name, "-p", "-S", "-", "-E", "-"
        )
        output = result.get("output", "") or ""
        if len(output) > MAX_OUTPUT_CHARS:
            output = "... [earlier output truncated]\n" + output[-MAX_OUTPUT_CHARS:]
        return output

    async def _execute_command(
        self,
        command: str,
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60,
    ) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # Set up working directory
            cwd = self.workspace_path
            if folder:
                folder = folder.strip("/")
                cwd = f"{self.workspace_path}/{folder}"

            # Generate a session name if not provided
            if not session_name:
                session_name = f"session_{str(uuid4())[:8]}"
            elif not _SESSION_NAME_RE.match(session_name):
                return self.fail_response(
                    "session_name may only contain letters, digits, '.', '_' and '-'"
                )

            if not await self._session_exists(session_name):
                await self._tmux("new-session", "-d", "-s", session_name)

            # Run in the requested directory; blocking commands print a unique
            # completion marker with their exit code.
            marker = f"__OM_DONE_{secrets.token_hex(6)}__"
            full_command = f"cd {shlex.quote(cwd)} && {command}"
            if blocking:
                full_command = f"{full_command}; echo {marker}$?"
            await self._tmux("send-keys", "-t", session_name, "-l", full_command)
            await self._tmux("send-keys", "-t", session_name, "Enter")

            if not blocking:
                return self.success_response(
                    {
                        "session_name": session_name,
                        "cwd": cwd,
                        "message": f"Command sent to tmux session '{session_name}'. Use check_command_output to view results.",
                        "completed": False,
                    }
                )

            done = re.compile(re.escape(marker) + r"(\d+)")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(1, timeout)
            exit_code: Optional[int] = None
            output = ""
            while loop.time() < deadline:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                if not await self._session_exists(session_name):
                    break
                output = await self._capture_pane(session_name)
                match = done.search(output)
                if match:
                    exit_code = int(match.group(1))
                    break
            else:
                output = await self._capture_pane(session_name)

            await self._tmux("kill-session", "-t", session_name)
            output = done.sub("", output.replace(f"; echo {marker}$?", ""))
            return self.success_response(
                {
                    "output": output,
                    "session_name": session_name,
                    "cwd": cwd,
                    "completed": exit_code is not None,
                    "exit_code": exit_code,
                }
            )

        except Exception as e:
            # Attempt to clean up session in case of error
            if session_name:
                try:
                    await self._tmux("kill-session", "-t", session_name)
                except Exception:
                    pass
            return self.fail_response(f"Error executing command: {str(e)}")

    async def _check_command_output(
        self, session_name: str, kill_session: bool = False
    ) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            if not await self._session_exists(session_name):
                return self.fail_response(
                    f"Tmux session '{session_name}' does not exist."
                )

            output = await self._capture_pane(session_name)

            # Kill session if requested
            if kill_session:
                await self._tmux("kill-session", "-t", session_name)
                termination_status = "Session terminated."
            else:
                termination_status = "Session still running."

            return self.success_response(
                {
                    "output": output,
                    "session_name": session_name,
                    "status": termination_status,
                }
            )

        except Exception as e:
            return self.fail_response(f"Error checking command output: {str(e)}")

    async def _terminate_command(self, session_name: str) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            if not await self._session_exists(session_name):
                return self.fail_response(
                    f"Tmux session '{session_name}' does not exist."
                )

            # Kill the session
            await self._tmux("kill-session", "-t", session_name)

            return self.success_response(
                {"message": f"Tmux session '{session_name}' terminated successfully."}
            )

        except Exception as e:
            return self.fail_response(f"Error terminating command: {str(e)}")

    async def _list_commands(self) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # List all tmux sessions
            result = await self._execute_raw_command(
                "tmux list-sessions 2>/dev/null || echo 'No sessions'"
            )
            output = result.get("output", "")

            if "No sessions" in output or not output.strip():
                return self.success_response(
                    {"message": "No active tmux sessions found.", "sessions": []}
                )

            # Parse session list
            sessions = []
            for line in output.split("\n"):
                if line.strip():
                    parts = line.split(":")
                    if parts:
                        session_name = parts[0].strip()
                        sessions.append(session_name)

            return self.success_response(
                {
                    "message": f"Found {len(sessions)} active sessions.",
                    "sessions": sessions,
                }
            )

        except Exception as e:
            return self.fail_response(f"Error listing commands: {str(e)}")

    async def execute(
        self,
        action: str,
        command: Optional[str] = None,
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60,
        kill_session: bool = False,
    ) -> ToolResult:
        """
        Execute a shell action in the sandbox environment.
        Args:
            action: The shell action to perform
            command: Command for execute_command
            folder: Sub-directory of /workspace to run the command in
            session_name: tmux session to use
            blocking: Wait for the command to finish
            timeout: Seconds to wait for blocking commands
            kill_session: Terminate the session after checking its output
        Returns:
            ToolResult with the action's output or error
        """
        async with self._lock:
            try:
                if action == "execute_command":
                    if not command:
                        return self.fail_response(
                            "command is required for execute_command"
                        )
                    return await self._execute_command(
                        command, folder, session_name, blocking, timeout
                    )
                elif action == "check_command_output":
                    if session_name is None:
                        return self.fail_response(
                            "session_name is required for check_command_output"
                        )
                    return await self._check_command_output(session_name, kill_session)
                elif action == "terminate_command":
                    if session_name is None:
                        return self.fail_response(
                            "session_name is required for terminate_command"
                        )
                    return await self._terminate_command(session_name)
                elif action == "list_commands":
                    return await self._list_commands()
                else:
                    return self.fail_response(f"Unknown action: {action}")
            except Exception as e:
                logger.error(f"Error executing shell action: {e}")
                return self.fail_response(f"Error executing shell action: {e}")

    async def cleanup(self):
        """Clean up all sessions."""
        if self._sandbox is None:
            return
        for session_name in list(self._sessions.keys()):
            await self._cleanup_session(session_name)

        # Also clean up any tmux sessions
        try:
            await self._execute_raw_command("tmux kill-server 2>/dev/null || true")
        except Exception as e:
            logger.error(f"Error shell box cleanup action: {e}")
