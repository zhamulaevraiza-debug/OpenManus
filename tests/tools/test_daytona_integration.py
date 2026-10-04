"""Daytona helpers with a fake SDK client: lazy client, events, thread offloading."""

import asyncio
import re
import threading
from types import SimpleNamespace

import pytest

from app.context import RunContext, reset_run, set_run
from app.daytona import sandbox as daytona_sandbox
from app.tool.computer_use_tool import ComputerUseTool
from app.tool.sandbox.sb_shell_tool import SandboxShellTool


class FakeProcess:
    def __init__(self):
        self.threads = set()
        self.commands = []
        self.pane = ""
        self.probes = 0

    def _record(self):
        self.threads.add(threading.get_ident())

    def exec(self, command, timeout=None):
        self._record()
        self.probes += 1
        return SimpleNamespace(result="000" if self.probes < 2 else "200", exit_code=0)

    def create_session(self, session_id):
        self._record()

    def delete_session(self, session_id):
        self._record()

    def execute_session_command(self, session_id, req, timeout=None):
        self._record()
        self.commands.append(req.command)
        marker = re.search(r"echo (__OM_DONE_[0-9a-f]+__)\$\?", req.command)
        if marker:
            self.pane = f"$ build\nbuilt ok\n{marker.group(1)}0\n$ "
        return SimpleNamespace(cmd_id=str(len(self.commands)), exit_code=0)

    def get_session_command_logs(self, session_id, command_id):
        command = self.commands[int(command_id) - 1]
        if "has-session" in command:
            return "" if "new-session" in "".join(self.commands) else "not_exists"
        if "capture-pane" in command:
            return self.pane
        return ""


class FakeSandbox:
    def __init__(self, sandbox_id="sb-1"):
        self.id = sandbox_id
        self.state = "started"
        self.process = FakeProcess()

    def get_preview_link(self, port):
        return SimpleNamespace(url=f"https://{port}-{self.id}.preview.example")


class FakeDaytona:
    def __init__(self, fail_services=False):
        self.created = []
        self.deleted = []
        self.fail_services = fail_services

    def create(self, params):
        sandbox = FakeSandbox(f"sb-{len(self.created) + 1}")
        sandbox.params = params
        if self.fail_services:
            sandbox.process.create_session = None  # not callable -> TypeError
        self.created.append(sandbox)
        return sandbox

    def delete(self, sandbox):
        self.deleted.append(sandbox.id)


@pytest.fixture
def fake_daytona(monkeypatch):
    client = FakeDaytona()
    monkeypatch.setattr(daytona_sandbox, "_client", client)
    monkeypatch.setattr(daytona_sandbox, "SERVICES_POLL_INTERVAL_SECONDS", 0)
    return client


@pytest.fixture
def events(workspace):
    collected = []
    token = set_run(
        RunContext(
            run_id="r",
            workspace=workspace,
            emit_sink=lambda kind, data: collected.append((kind, data)),
        )
    )
    try:
        yield collected
    finally:
        reset_run(token)


def test_client_requires_api_key(monkeypatch):
    monkeypatch.setattr(daytona_sandbox, "_client", None)
    monkeypatch.setattr(daytona_sandbox, "daytona_configured", lambda: False)
    with pytest.raises(RuntimeError, match="Daytona API key not configured"):
        daytona_sandbox.get_daytona()


@pytest.mark.asyncio
async def test_provision_emits_ready_event_with_random_password(fake_daytona, events):
    loop_thread = threading.get_ident()
    first = await daytona_sandbox.provision_sandbox()
    second = await daytona_sandbox.provision_sandbox()

    ready = [data for kind, data in events if kind == "sandbox.ready"]
    assert [event["vnc_url"] for event in ready] == [
        "https://6080-sb-1.preview.example",
        "https://6080-sb-2.preview.example",
    ]
    assert ready[0]["website_url"] == "https://8080-sb-1.preview.example"
    passwords = [sb.params.env_vars["VNC_PASSWORD"] for sb in (first, second)]
    assert passwords == [event["vnc_password"] for event in ready]
    assert passwords[0] != passwords[1] and len(passwords[0]) >= 12
    assert "123456" not in passwords
    assert loop_thread not in first.process.threads  # SDK calls ran in threads
    assert first.process.probes >= 2  # waited until the services answered


@pytest.mark.asyncio
async def test_blocking_create_runs_in_worker_thread_without_events(
    fake_daytona, events
):
    sandbox = await asyncio.to_thread(daytona_sandbox.create_sandbox, password="pw")
    assert sandbox.params.env_vars["VNC_PASSWORD"] == "pw"
    assert events == []


def test_blocking_create_refuses_event_loop_thread(fake_daytona):
    async def call_on_loop():
        daytona_sandbox.create_sandbox()

    with pytest.raises(RuntimeError):
        asyncio.run(call_on_loop())


@pytest.mark.asyncio
async def test_failed_start_deletes_sandbox(monkeypatch, events):
    client = FakeDaytona(fail_services=True)
    monkeypatch.setattr(daytona_sandbox, "_client", client)
    with pytest.raises(TypeError):
        await daytona_sandbox.provision_sandbox()
    assert client.deleted == ["sb-1"]
    assert events == []


@pytest.mark.asyncio
async def test_shell_tool_blocking_command(fake_daytona):
    sandbox = FakeSandbox()
    tool = SandboxShellTool(sandbox)
    result = await tool.execute(
        action="execute_command",
        command="make 'all' && echo \"$HOME\"",
        folder="proj dir",
        blocking=True,
        timeout=5,
    )
    assert result.error is None, result.error
    assert '"exit_code": 0' in result.output and "built ok" in result.output
    assert "__OM_DONE" not in result.output
    send_keys = next(
        c for c in sandbox.process.commands if "send-keys" in c and "-l" in c
    )
    assert "'cd '\"'\"'/workspace/proj dir'\"'\"' && make" in send_keys
    assert threading.get_ident() not in sandbox.process.threads


@pytest.mark.asyncio
async def test_shell_tool_actions_without_command():
    tool = SandboxShellTool(FakeSandbox())
    result = await tool.execute(action="check_command_output")
    assert result.error == "session_name is required for check_command_output"


@pytest.mark.asyncio
async def test_computer_screenshot_is_saved_in_workspace(run_ctx, monkeypatch):
    tool = ComputerUseTool(FakeSandbox())

    async def fake_request(method, endpoint, data=None):
        return {"image": "iVBORw0KGgo="}

    monkeypatch.setattr(tool, "_api_request", fake_request)
    result = await tool.execute(action="screenshot")
    saved = result.output.removeprefix("Screenshot saved as ")
    assert saved.startswith("screenshots/")
    assert (run_ctx.workspace / saved).read_bytes().startswith(b"\x89PNG")
    assert result.base64_image == "iVBORw0KGgo="
    assert await tool._get_api_base_url() == "https://8000-sb-1.preview.example"
    await tool.cleanup()
