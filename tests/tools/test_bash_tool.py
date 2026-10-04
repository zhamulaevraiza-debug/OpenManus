"""Tests for the persistent bash tool (sentinel protocol, limits, lifecycle)."""

import asyncio
import time

import pytest
import pytest_asyncio

from app.exceptions import ToolError
from app.tool.bash import MAX_OUTPUT_BYTES, Bash
from app.tool.tool_collection import ToolCollection


pytestmark = pytest.mark.asyncio


def _pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


async def _wait_dead(pid: int) -> bool:
    for _ in range(60):
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest_asyncio.fixture
async def bash(run_ctx):
    tool = Bash(timeout=3)
    try:
        yield tool
    finally:
        await tool.cleanup()


async def run(tool: Bash, command: str) -> str:
    return str(await tool.execute(command=command))


async def test_starts_in_workspace_and_keeps_state(bash, run_ctx):
    assert await run(bash, "pwd") == str(run_ctx.workspace)
    await run(bash, "mkdir sub && cd sub && export GREETING=hi")
    assert await run(bash, "pwd; echo $GREETING") == f"{run_ctx.workspace}/sub\nhi"


async def test_empty_command(bash):
    assert await run(bash, "") == ""
    assert await run(bash, "echo still-works") == "still-works"


async def test_trailing_ampersand(bash):
    started = time.monotonic()
    assert await run(bash, "sleep 30 > /dev/null 2>&1 &") == ""
    assert time.monotonic() - started < 2
    assert await run(bash, "echo after") == "after"


async def test_trailing_comment(bash):
    assert await run(bash, "echo visible # a comment") == "visible"


async def test_output_without_trailing_newline(bash):
    assert await run(bash, "printf 'no newline'") == "no newline"


async def test_stderr_and_exit_code_are_reported(bash):
    out = await run(bash, "echo out; echo err >&2; false")
    assert out == "out\n[stderr]\nerr\n[exit code: 1]"


async def test_syntax_error_does_not_break_session(bash):
    out = await run(bash, "if true; then echo unterminated")
    assert "syntax error" in out
    assert await run(bash, "echo ok") == "ok"


async def test_commands_reading_stdin_do_not_hang(bash):
    started = time.monotonic()
    assert await run(bash, "cat") == ""
    assert time.monotonic() - started < 2


async def test_quotes_and_special_characters(bash):
    assert await run(bash, "echo \"a'b\" '$HOME' `echo x`") == "a'b $HOME x"


async def test_large_output_is_drained_and_capped(bash):
    started = time.monotonic()
    out = await run(bash, "head -c 300000 /dev/zero | tr '\\0' 'a'; echo; echo END")
    assert time.monotonic() - started < 3
    assert out.endswith("END")
    assert "bytes of output truncated" in out
    assert len(out) < MAX_OUTPUT_BYTES + 200
    assert await run(bash, "echo next") == "next"


async def test_timeout_restarts_session(bash, run_ctx):
    await run(bash, "cd / ")
    result = await bash.execute(command="echo partial; sleep 30")
    assert result.error and "timed out" in result.error
    assert "partial" in result.error
    assert await run(bash, "pwd") == str(run_ctx.workspace)


async def test_shell_exit_is_reported_and_recovered(bash):
    result = await bash.execute(command="exit 3")
    assert result.error and "code 3" in result.error
    assert await run(bash, "echo back") == "back"


async def test_restart(bash, run_ctx):
    await run(bash, "cd /tmp")
    result = await bash.execute(restart=True)
    assert str(result) == "tool has been restarted."
    assert await run(bash, "pwd") == str(run_ctx.workspace)


async def test_ctrl_c_kills_processes(bash):
    pid = int(await run(bash, "sleep 60 > /dev/null 2>&1 & echo $!"))
    assert "restarted" in await run(bash, "ctrl+c")
    assert await _wait_dead(pid)


async def test_cleanup_kills_process_group(bash):
    pid = int(await run(bash, "sleep 60 > /dev/null 2>&1 & echo $!"))
    assert _pid_alive(pid)
    await bash.cleanup()
    assert await _wait_dead(pid)


async def test_cancellation_kills_running_command(bash):
    pid = int(await run(bash, "echo $$"))
    task = asyncio.ensure_future(bash.execute(command="sleep 30"))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_dead(pid)
    assert await run(bash, "echo fresh") == "fresh"


async def test_missing_command_is_a_tool_error(bash):
    with pytest.raises(ToolError):
        await bash.execute()
    failure = await ToolCollection(bash).execute(name="bash", tool_input=None)
    assert failure.error == "no command provided."


async def test_instances_have_separate_sessions(run_ctx):
    first, second = Bash(), Bash()
    try:
        await first.execute(command="export ONLY_FIRST=1")
        assert await run(second, "echo ${ONLY_FIRST:-unset}") == "unset"
    finally:
        await first.cleanup()
        await second.cleanup()


async def test_schema_exposes_restart():
    assert "restart" in Bash().parameters["properties"]
