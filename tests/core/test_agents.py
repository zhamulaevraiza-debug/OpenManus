import subprocess
import sys

import pytest

from app.agent.base import STUCK_PROMPT
from app.agent.manus import Manus
from app.agent.mcp import MCPAgent
from app.agent.researcher import ResearcherAgent
from app.agent.swe import SWEAgent
from app.agent.toolcall import EVENT_OUTPUT_LIMIT, ToolCallAgent
from app.agent.writer import WriterAgent
from app.config import PROJECT_ROOT, config
from app.tool.base import BaseTool, ToolResult
from app.tool.mcp import MCPClients, MCPClientTool
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection


class EchoTool(BaseTool):
    name: str = "echo"
    description: str = "Echo text"
    parameters: dict = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, text: str = "") -> ToolResult:
        if text == "fail":
            return ToolResult(error="it failed")
        return ToolResult(output=text, base64_image="aW1n" if text == "image" else None)


def _agent(**kwargs) -> ToolCallAgent:
    return ToolCallAgent(
        name="tester",
        available_tools=ToolCollection(EchoTool(), Terminate()),
        **kwargs,
    )


@pytest.mark.parametrize(
    "agent_class", [ToolCallAgent, SWEAgent, Manus, ResearcherAgent, WriterAgent]
)
def test_tool_collections_are_per_instance(agent_class, config_dir):
    first, second = agent_class(), agent_class()
    assert first.available_tools is not second.available_tools
    for name, tool in first.available_tools.tool_map.items():
        assert tool is not second.available_tools.tool_map[name]
    first.available_tools.add_tool(EchoTool())
    assert "echo" not in second.available_tools.tool_map


def test_prompts_use_the_current_workspace(config_dir, workspace, run_context):
    for agent in (Manus(), SWEAgent(), ResearcherAgent(), WriterAgent()):
        assert str(workspace) in agent.system_prompt
        assert "{" not in agent.system_prompt


def test_prompts_use_the_configured_workspace_outside_runs(config_dir):
    assert str(config.workspace_root) in Manus().system_prompt


@pytest.mark.asyncio
async def test_run_emits_events_and_resets_step_counter(fake_llm, run_context, events):
    calls = iter(
        [
            fake_llm.reply("Let me echo", fake_llm.tool_call("echo", text="image")),
            fake_llm.reply("Done", fake_llm.tool_call("terminate", status="success")),
        ]
        * 2
    )
    fake_llm.agent = lambda system, messages, tools: next(calls)
    agent = _agent()

    await agent.run("first")
    assert agent.finish_reason == "terminated"
    assert events.types() == [
        "agent.started",
        "agent.step",
        "agent.thought",
        "tool.call",
        "tool.result",
        "agent.step",
        "agent.thought",
        "tool.call",
        "tool.result",
        "agent.finished",
    ]
    started, finished = events.of("agent.started")[0], events.of("agent.finished")[0]
    assert started == {
        "agent": "tester",
        "title": "tester",
        "max_steps": agent.max_steps,
    }
    assert finished == {"agent": "tester", "steps": 2, "reason": "terminated"}
    call, result = events.of("tool.call")[0], events.of("tool.result")[0]
    assert call["arguments"] == {"text": "image"} and call["step"] == 1
    assert result["call_id"] == call["call_id"]
    assert result["image_b64"] == "aW1n" and result["error"] is False
    assert "image_b64" not in events.of("tool.result")[1]

    events.events.clear()
    await agent.run("second")  # the same instance can run again from step 1
    assert events.of("agent.step")[0]["step"] == 1
    assert events.of("agent.finished")[0]["steps"] == 2


@pytest.mark.asyncio
async def test_tool_results_are_truncated_and_errors_flagged(
    fake_llm, run_context, events
):
    calls = iter(
        [
            fake_llm.reply(
                None,
                fake_llm.tool_call("echo", text="x" * 20000),
                fake_llm.tool_call("echo", text="fail"),
                fake_llm.tool_call("missing_tool"),
            ),
            fake_llm.reply("bye", fake_llm.tool_call("terminate", status="success")),
        ]
    )
    fake_llm.agent = lambda system, messages, tools: next(calls)
    await _agent().run("go")

    big, failed, unknown = events.of("tool.result")[:3]
    assert len(big["output"]) <= EVENT_OUTPUT_LIMIT and "truncated" in big["output"]
    assert big["error"] is False
    assert failed["error"] is True and "it failed" in failed["output"]
    assert unknown["error"] is True
    # Thoughts are only reported when the model wrote some text
    assert [t["content"] for t in events.of("agent.thought")] == ["bye"]


@pytest.mark.asyncio
async def test_text_reply_finishes_the_run(fake_llm, run_context, events):
    fake_llm.agent = lambda system, messages, tools: fake_llm.reply("The answer is 4.")
    agent = _agent()
    await agent.run("2+2?")
    assert events.of("agent.finished") == [
        {"agent": "tester", "steps": 1, "reason": "no_action"}
    ]


@pytest.mark.asyncio
async def test_max_steps_reason(fake_llm, run_context, events):
    fake_llm.agent = lambda system, messages, tools: fake_llm.reply(
        None, fake_llm.tool_call("echo", text=str(len(messages)))
    )
    await _agent(max_steps=3).run("loop")
    assert events.of("agent.finished")[0] == {
        "agent": "tester",
        "steps": 3,
        "reason": "max_steps",
    }


@pytest.mark.asyncio
async def test_stuck_hint_is_applied_for_one_step_only(fake_llm, run_context, events):
    responses = iter(
        [fake_llm.reply("same", fake_llm.tool_call("echo", text="a")) for _ in range(3)]
        + [fake_llm.reply("different", fake_llm.tool_call("echo", text="b"))]
        + [fake_llm.reply("done", fake_llm.tool_call("terminate", status="success"))]
    )
    fake_llm.agent = lambda system, messages, tools: next(responses)
    agent = _agent()
    original_prompt = agent.next_step_prompt

    await agent.run("go")

    hinted = [
        m
        for m in agent.memory.messages
        if m.role == "user" and STUCK_PROMPT in (m.content or "")
    ]
    assert len(hinted) == 1
    assert hinted[0].content.endswith(original_prompt)
    assert agent.next_step_prompt == original_prompt
    assert events.of("agent.stuck") == [{"agent": "tester", "step": 3}]


def test_mcp_terminate_matches_original_tool_name(config_dir):
    agent = MCPAgent()
    clients = MCPClients()
    tool = MCPClientTool(
        name="mcp_server_terminate",
        description="",
        parameters={},
        server_id="server",
        original_name="terminate",
    )
    clients.tool_map[tool.name] = tool
    clients.tools = (tool,)
    agent.available_tools = clients
    assert agent._is_special_tool("mcp_server_terminate")
    assert not agent.cleanup_after_run


@pytest.mark.asyncio
async def test_global_sandbox_cleanup_only_without_run_context(
    fake_llm, monkeypatch, events
):
    from app.context import RunContext, reset_run, set_run
    from app.sandbox.client import SANDBOX_CLIENT

    cleanups = []

    async def record_cleanup():
        cleanups.append(True)

    monkeypatch.setattr(SANDBOX_CLIENT, "cleanup", record_cleanup)
    await _agent().run("cli run")
    assert cleanups == [True]

    token = set_run(
        RunContext(run_id="r", workspace=config.workspace_root, emit_sink=events)
    )
    try:
        await _agent().run("web run")
    finally:
        reset_run(token)
    assert cleanups == [True]


def test_browser_agent_import_does_not_load_daytona():
    code = "import sys, app.agent.browser; print('app.daytona' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


@pytest.mark.asyncio
async def test_browser_state_screenshot_is_not_repeated_after_an_action_screenshot(
    config_dir, monkeypatch
):
    from app.agent.browser import BrowserAgent
    from app.schema import Message
    from app.tool.browser_use_tool import BrowserUseTool

    async def state(self):
        return ToolResult(output='{"url": "u", "title": "t"}', base64_image="STATE")

    monkeypatch.setattr(BrowserUseTool, "get_current_state", state)
    agent = BrowserAgent()
    helper = agent.browser_context_helper

    agent.memory.add_message(
        Message.tool_message("Navigated", "browser_use", "c1", base64_image="ACTION")
    )
    await helper.format_next_step_prompt()
    assert [m.base64_image for m in agent.memory.messages] == ["ACTION"]

    agent.memory.add_message(Message.tool_message("Extracted", "browser_use", "c2"))
    await helper.format_next_step_prompt()
    assert agent.memory.messages[-1].content == "Current browser screenshot:"
    assert agent.memory.messages[-1].base64_image == "STATE"


@pytest.mark.asyncio
async def test_sandbox_agent_creates_and_deletes_its_sandbox(
    config_dir, run_context, monkeypatch
):
    from types import SimpleNamespace

    import app.daytona.sandbox as daytona_sandbox
    from app.agent.sandbox_agent import SandboxManus

    sandbox = SimpleNamespace(id="sb-1")
    provisioned, deleted = [], []

    async def provision_sandbox(password=None, project_id=None):
        provisioned.append(password)
        return sandbox

    async def get_sandbox_links(sb):
        return "https://vnc.test", "https://site.test"

    async def delete_sandbox(sandbox_id):
        deleted.append(sandbox_id)
        return True

    monkeypatch.setattr(daytona_sandbox, "provision_sandbox", provision_sandbox)
    monkeypatch.setattr(daytona_sandbox, "get_sandbox_links", get_sandbox_links)
    monkeypatch.setattr(daytona_sandbox, "delete_sandbox", delete_sandbox)

    agent = await SandboxManus.create()
    assert provisioned == [None]  # web runs get a random VNC password
    assert {
        "sandbox_browser",
        "sandbox_files",
        "sandbox_shell",
        "sandbox_vision",
    } <= set(agent.available_tools.tool_map)
    assert agent.sandbox_link == {
        "sb-1": {"vnc": "https://vnc.test", "website": "https://site.test"}
    }
    assert "/workspace" in agent.system_prompt

    await agent.cleanup()
    await agent.cleanup()  # idempotent
    assert deleted == ["sb-1"] and agent.sandbox is None


@pytest.mark.asyncio
async def test_sandbox_agent_cleanup_without_sandbox(config_dir):
    from app.agent.sandbox_agent import SandboxManus

    await SandboxManus().cleanup()
