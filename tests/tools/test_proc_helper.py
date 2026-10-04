"""Tests for app.utils.proc (environment scrubbing, privilege drop, killing)."""

import asyncio
import os
import pwd
import signal
import sys
from types import SimpleNamespace

import pytest

from app.utils import proc
from app.utils.proc import (
    CappedBuffer,
    kill_process_tree,
    run_process,
    scrubbed_env,
    subprocess_kwargs,
)


SECRETS = {
    "OPENAI_API_KEY": "sk-secret",
    "ANTHROPIC_API_KEY": "secret",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_ACCESS_KEY_ID": "id",
    "OPENMANUS_SECRET_KEY": "secret",
    "OPENMANUS_ADMIN_PASSWORD": "secret",
    "DAYTONA_API_KEY": "secret",
    "GH_TOKEN": "secret",
    "DB_PASSWORD": "secret",
}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        # The process can exit between kill(0) and reading /proc.
        return False


def test_scrubbed_env_never_leaks_secrets(monkeypatch, run_ctx):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SAFE_SETTING", "visible")
    monkeypatch.setenv(
        "OPENMANUS_EXEC_ENV_ALLOWLIST", ",".join([*SECRETS, "SAFE_SETTING"])
    )

    env = scrubbed_env()

    assert not set(SECRETS) & set(env)
    assert env["SAFE_SETTING"] == "visible"
    assert env["HOME"] == str(run_ctx.workspace)
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["MPLBACKEND"] == "Agg"
    assert env["PATH"]


def test_scrubbed_env_extra_is_applied(run_ctx):
    env = scrubbed_env({"HOME": "/elsewhere", "TERM": "dumb"})
    assert env["HOME"] == "/elsewhere"
    assert env["TERM"] == "dumb"


def test_subprocess_kwargs_defaults(monkeypatch, run_ctx):
    monkeypatch.delenv("OPENMANUS_EXEC_USER", raising=False)
    kwargs = subprocess_kwargs()
    assert kwargs["cwd"] == str(run_ctx.workspace)
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["HOME"] == str(run_ctx.workspace)
    assert "user" not in kwargs


def test_subprocess_kwargs_drops_privileges_when_root(monkeypatch, run_ctx):
    monkeypatch.setenv("OPENMANUS_EXEC_USER", "agent")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=10001, pw_gid=10002)
    )
    kwargs = subprocess_kwargs()
    assert kwargs["user"] == 10001
    assert kwargs["group"] == 10002
    assert kwargs["extra_groups"] == []


def test_subprocess_kwargs_keeps_user_when_not_root(monkeypatch, run_ctx):
    monkeypatch.setenv("OPENMANUS_EXEC_USER", "agent")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert "user" not in subprocess_kwargs()


def test_capped_buffer_keeps_head_and_tail():
    buffer = CappedBuffer(90)
    buffer.feed(b"H" * 100)
    buffer.feed(b"T" * 100)
    text = buffer.text()
    assert text.startswith("H" * 60)
    assert text.endswith("T" * 30)
    assert "[110 bytes of output truncated]" in text


@pytest.mark.asyncio
async def test_kill_process_tree_kills_background_children(run_ctx):
    proc_ = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-c",
        "sleep 60 & echo $!; sleep 60 & echo $!; wait",
        stdout=asyncio.subprocess.PIPE,
        **subprocess_kwargs(),
    )
    children = [int(await proc_.stdout.readline()) for _ in range(2)]
    assert all(_pid_alive(pid) for pid in children)

    await kill_process_tree(proc_)

    assert proc_.returncode is not None
    for _ in range(50):
        if not any(_pid_alive(pid) for pid in children):
            break
        await asyncio.sleep(0.05)
    assert not any(_pid_alive(pid) for pid in children)


@pytest.mark.asyncio
async def test_kill_process_tree_escalates_to_sigkill(run_ctx):
    proc_ = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        **subprocess_kwargs(),
    )
    await proc_.stdout.readline()
    await kill_process_tree(proc_)
    assert proc_.returncode == -signal.SIGKILL


@pytest.mark.asyncio
async def test_run_process_captures_and_caps_output(run_ctx):
    result = await run_process(
        sys.executable,
        "-c",
        "import sys; print('x' * 50000); print('err', file=sys.stderr)",
        output_limit=1000,
    )
    assert result.returncode == 0
    assert "bytes of output truncated" in result.stdout
    assert len(result.stdout) < 1200
    assert result.stderr.strip() == "err"


@pytest.mark.asyncio
async def test_run_process_timeout_kills_group(run_ctx):
    result = await run_process("sleep 30 & echo $!; wait", shell=True, timeout=0.5)
    assert result.timed_out
    background_pid = int(result.stdout.split()[0])
    for _ in range(50):
        if not _pid_alive(background_pid):
            break
        await asyncio.sleep(0.05)
    assert not _pid_alive(background_pid)


@pytest.mark.asyncio
async def test_run_process_cancellation_kills_child(run_ctx, workspace):
    pid_file = workspace / "pid"
    task = asyncio.ensure_future(
        run_process("echo $$ > pid; exec sleep 30", shell=True, timeout=60)
    )
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
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
    assert not _pid_alive(pid)


@pytest.mark.asyncio
async def test_run_process_uses_workspace_and_scrubbed_env(monkeypatch, run_ctx):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    result = await run_process("pwd; env", shell=True)
    lines = result.stdout.splitlines()
    assert lines[0] == str(run_ctx.workspace)
    assert not any(line.startswith("OPENAI_API_KEY=") for line in lines)


def test_exec_user_ids_requires_existing_user(monkeypatch):
    monkeypatch.setenv("OPENMANUS_EXEC_USER", "no-such-user-xyz")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert proc.exec_user_ids() is None
