"""Tests for the subprocess based python_execute tool."""

import asyncio
import os
import time

import pytest

from app.tool.python_execute import MAX_OUTPUT_CHARS, PythonExecute


pytestmark = pytest.mark.asyncio


def _pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


async def test_prints_are_captured(run_ctx):
    result = await PythonExecute().execute(code="print('hello'); print(6 * 7)")
    assert result == {"observation": "hello\n42\n", "success": True}


async def test_runs_in_workspace_with_scrubbed_env(monkeypatch, run_ctx):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    result = await PythonExecute().execute(
        code="import os; print(os.getcwd()); print('OPENAI_API_KEY' in os.environ)"
    )
    cwd, has_key = result["observation"].splitlines()
    assert os.path.samefile(cwd, run_ctx.workspace)
    assert has_key == "False"


async def test_files_and_modules_live_in_workspace(run_ctx):
    (run_ctx.workspace / "helper.py").write_text("VALUE = 7\n")
    result = await PythonExecute().execute(
        code="import helper\nopen('out.txt', 'w').write(str(helper.VALUE))"
    )
    assert result["success"], result
    assert (run_ctx.workspace / "out.txt").read_text() == "7"


async def test_exception_reports_traceback(run_ctx):
    result = await PythonExecute().execute(code="x = 1\nraise ValueError('boom')")
    assert result["success"] is False
    assert "ValueError: boom" in result["observation"]
    assert 'File "<python_execute>", line 2' in result["observation"]


async def test_stderr_is_included(run_ctx):
    result = await PythonExecute().execute(
        code="import sys; print('warn', file=sys.stderr)"
    )
    assert result == {"observation": "warn\n", "success": True}


async def test_timeout_kills_and_keeps_partial_output(run_ctx):
    started = time.monotonic()
    result = await PythonExecute().execute(
        code="import time\nprint('started', flush=True)\ntime.sleep(30)", timeout=1
    )
    assert time.monotonic() - started < 5
    assert result["success"] is False
    assert result["observation"].startswith("started")
    assert "Execution timeout after 1 seconds" in result["observation"]


async def test_output_is_capped(run_ctx):
    result = await PythonExecute().execute(code="print('x' * 200000)")
    assert result["success"]
    assert len(result["observation"]) < MAX_OUTPUT_CHARS + 200
    assert "truncated" in result["observation"]


async def test_does_not_block_the_event_loop(run_ctx):
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    task = asyncio.ensure_future(ticker())
    try:
        await PythonExecute().execute(code="import time; time.sleep(1.5)")
    finally:
        task.cancel()
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 20
    assert max(gaps) < 0.5


async def test_cancellation_kills_the_interpreter(run_ctx):
    pid_file = run_ctx.workspace / "pid"
    task = asyncio.ensure_future(
        PythonExecute().execute(
            code="import os, time\nopen('pid', 'w').write(str(os.getpid()))\n"
            "time.sleep(30)",
            timeout=60,
        )
    )
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.02)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if not _pid_alive(pid):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("python_execute child survived cancellation")
