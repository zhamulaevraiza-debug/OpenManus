import sys
from typing import Dict

from app.context import get_workspace
from app.tool.base import BaseTool
from app.utils.proc import run_process


DEFAULT_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 20000

# Executed with ``python -I -u -c``: reads the user code from stdin and runs it as
# ``__main__`` with the workspace importable. Tracebacks hide the runner's own frame.
_RUNNER = """\
import os, sys, traceback
sys.path.insert(0, os.getcwd())
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
_source = sys.stdin.buffer.read().decode("utf-8", errors="replace")
sys.stdin = open(os.devnull)
_globals = {"__name__": "__main__", "__builtins__": __builtins__}
try:
    exec(compile(_source, "<python_execute>", "exec"), _globals)
except SystemExit:
    raise
except BaseException:
    _type, _value, _tb = sys.exc_info()
    traceback.print_exception(_type, _value, _tb.tb_next)
    sys.exit(1)
"""


class PythonExecute(BaseTool):
    """A tool for executing Python code with timeout and safety restrictions."""

    name: str = "python_execute"
    description: str = "Executes Python code string. Note: Only print outputs are visible, function return values are not captured. Use print statements to see results."
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
        },
        "required": ["code"],
    }

    async def execute(
        self,
        code: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Dict:
        """
        Executes the provided Python code in a separate process with a timeout.

        The code runs in a fresh interpreter whose working directory is the current
        workspace, with a scrubbed environment (no server secrets) and, when
        configured, as the unprivileged exec user. stdout and stderr are captured
        together and capped at ``MAX_OUTPUT_CHARS``.

        Args:
            code (str): The Python code to execute.
            timeout (int): Execution timeout in seconds.

        Returns:
            Dict: Contains 'observation' with the captured output (or error traceback)
            and 'success' status.
        """
        workspace = get_workspace()
        workspace.mkdir(parents=True, exist_ok=True)
        result = await run_process(
            sys.executable,
            "-I",
            "-u",
            "-c",
            _RUNNER,
            stdin_data=code.encode("utf-8"),
            timeout=timeout,
            output_limit=MAX_OUTPUT_CHARS,
            merge_stderr=True,
            cwd=workspace,
        )
        if result.timed_out:
            observation = result.stdout.rstrip("\n")
            notice = f"Execution timeout after {timeout} seconds"
            return {
                "observation": f"{observation}\n{notice}" if observation else notice,
                "success": False,
            }
        return {"observation": result.stdout, "success": result.returncode == 0}
