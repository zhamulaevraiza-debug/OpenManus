"""Tests of the fake model: its script, its OpenAI wire protocol and the real core
(router, agents, team flow, tools) running against it over HTTP."""

import asyncio
import base64
import json
import os
import socket
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest import mock

import httpx
import pytest
import uvicorn
from openai import AsyncOpenAI

from app.config import config
from app.context import RunContext, reset_run, set_run
from app.flow.runner import run_task

from .script import ASK_HUMAN_TOOL, PYTHON_TOOL, TERMINATE_TOOL, respond
from .server import FakeLLMSettings, create_app


def _tools(*names: str) -> List[Dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": name, "parameters": {}}}
        for name in names
    ]


def _user(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": text}


def _call(name: str, call_id: str) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name}}],
    }


def _result(name: str, call_id: str, text: str) -> Dict[str, Any]:
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": f"Observed output of cmd `{name}` executed:\n{text}",
    }


# ------------------------------------------------------------------- script


@pytest.mark.parametrize(
    "request_text, mode, agent",
    [
        ("Привет! Как дела?", "chat", None),
        ("hello there", "chat", None),
        ("Create the file hello.txt", "agent", "manus"),
        ("Создай файл hello.txt", "agent", "manus"),
        ("Please ask me what to write", "agent", "manus"),
        ("Do this task", "chat", None),  # "task" contains "ask" but is no keyword
        ("Use the team to build a report", "team", None),
        ("Browse https://example.org and take a screenshot", "agent", "browser"),
        ("Открой сайт example.org", "agent", "browser"),
        ("Командой подготовь отчёт", "team", None),
    ],
)
def test_router_decides_by_keywords(request_text, mode, agent):
    prompt = f"Conversation so far:\nUser: hi\n\nLatest user request:\n{request_text}"
    reply = respond([_user(prompt)], _tools("route"))

    (call,) = reply.tool_calls
    assert call.name == "route"
    assert call.arguments["mode"] == mode
    assert call.arguments.get("agent") == agent
    assert call.arguments["reason"]


def test_planner_creates_two_tagged_steps_for_available_agents():
    system = (
        "Available agents (tag: description):\n- [manus] General\n"
        "- [writer] Writes\n- [coder] Codes\n"
    )
    messages = [
        {"role": "system", "content": system},
        _user("User request:\nTeam, please do it"),
    ]

    (call,) = respond(messages, _tools("planning")).tool_calls

    assert call.arguments["command"] == "create"
    assert call.arguments["steps"] == [
        "[coder] Create hello.txt with a friendly greeting",
        "[writer] Write summary.md describing hello.txt",
    ]


def test_planner_answers_in_russian_and_reuses_a_single_agent():
    messages = [
        {"role": "system", "content": "- [manus] General"},
        _user("User request:\nСделай командой"),
    ]

    (call,) = respond(messages, _tools("planning")).tool_calls

    assert call.arguments["title"] == "Файл приветствия и сводка"
    assert [step[:7] for step in call.arguments["steps"]] == ["[manus]", "[manus]"]


def test_agent_writes_the_file_then_terminates():
    tools = _tools(PYTHON_TOOL, TERMINATE_TOOL, ASK_HUMAN_TOOL)
    history = [_user("earlier question"), {"role": "assistant", "content": "answer"}]
    request = _user("Create the file notes.txt")
    next_step = _user("When the task is complete, state the created files")

    first = respond(history + [request, next_step], tools)
    (write,) = first.tool_calls
    assert write.name == PYTHON_TOOL
    assert (
        "Path('notes.txt').write_text('Hello from OpenManus!\\n'"
        in write.arguments["code"]
    )
    assert first.content and not first.slow

    done = respond(
        history
        + [request, next_step, _call(PYTHON_TOOL, "c1")]
        + [_result(PYTHON_TOOL, "c1", "{'observation': 'Wrote', 'success': True}")]
        + [next_step],
        tools,
    )
    assert [call.name for call in done.tool_calls] == [TERMINATE_TOOL]
    assert done.tool_calls[0].arguments == {"status": "success"}
    assert done.content == "Created `notes.txt` in the workspace."


def test_agent_asks_the_human_first_and_writes_the_answer():
    tools = _tools(PYTHON_TOOL, TERMINATE_TOOL, ASK_HUMAN_TOOL)
    request = _user("slow: ask me what to put into hello.txt")

    ask = respond([request], tools)
    assert [call.name for call in ask.tool_calls] == [ASK_HUMAN_TOOL]
    assert ask.slow

    write = respond(
        [request, _call(ASK_HUMAN_TOOL, "a1"), _result(ASK_HUMAN_TOOL, "a1", "Hi!")],
        tools,
    )
    (call,) = write.tool_calls
    assert call.name == PYTHON_TOOL
    assert "'Hi!\\n'" in call.arguments["code"]


def test_attached_files_are_not_mistaken_for_the_file_to_write():
    request = _user(
        "Read the attached file\n\nAttached files (paths relative to the "
        "workspace):\n- uploads/notes.txt"
    )

    (call,) = respond([request], _tools(PYTHON_TOOL)).tool_calls

    assert "Path('hello.txt')" in call.arguments["code"]


def test_team_step_task_comes_from_the_step_line():
    step_prompt = (
        "User request:\nask the team for hello.txt\n\nYour step (2/2): "
        "Write summary.md describing hello.txt\n\nShared workspace directory: /w"
    )

    (call,) = respond([_user(step_prompt)], _tools(PYTHON_TOOL)).tool_calls

    assert call.name == PYTHON_TOOL  # no question: "ask" is not in the step itself
    assert "Path('summary.md')" in call.arguments["code"]


def test_final_answer_summarises_files_and_team_steps():
    prompt = (
        "User request:\nTeam: create files\n\nWork record:\nPlan: Greeting\n\n"
        "Step 1 [coder]: Create hello.txt\nStatus: completed\nResult: Created `hello.txt`\n\n"
        "Step 2 [writer]: Write summary.md\nStatus: blocked\nResult: Error\n\n"
        "Write the final answer now."
    )

    reply = respond([{"role": "system", "content": "final"}, _user(prompt)])

    assert reply.kind == "answer" and not reply.tool_calls
    assert "- `hello.txt`\n- `summary.md`" in reply.content
    assert "| 1 | Create hello.txt | coder | ✅ completed |" in reply.content
    assert "| 2 | Write summary.md | writer | ⚠️ blocked |" in reply.content


def test_chat_replies_greet_in_the_user_language():
    assert respond([_user("привет")]).content.startswith("Привет! 👋")
    assert respond([_user("Hello")]).content.startswith("Hello! 👋")
    assert "“What is 2+2?”" in respond([_user("What is 2+2?")]).content


# ------------------------------------------------------------- HTTP protocol


@pytest.fixture
def http_client():
    app = create_app(FakeLLMSettings(slow_seconds=0, chunk_delay=0))
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake")
    yield app, client


@pytest.mark.asyncio
async def test_openai_client_parses_tool_calls_and_streams(http_client):
    app, client = http_client
    openai = AsyncOpenAI(base_url="http://fake/v1", api_key="k", http_client=client)

    response = await openai.chat.completions.create(
        model="any",
        messages=[_user("Latest user request:\nteam work")],
        tools=_tools("route"),
        tool_choice="required",
    )
    (call,) = response.choices[0].message.tool_calls
    assert json.loads(call.function.arguments)["mode"] == "team"
    assert response.usage.total_tokens > 0

    stream = await openai.chat.completions.create(
        model="any", messages=[_user("hello")], stream=True
    )
    text, usage = [], None
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text.append(chunk.choices[0].delta.content)
        usage = chunk.usage or usage
    assert len(text) > 3
    assert "".join(text) == respond([_user("hello")]).content
    assert usage is not None and usage.completion_tokens > 0

    stream = await openai.chat.completions.create(
        model="any",
        messages=[_user("Create hello.txt")],
        tools=_tools(PYTHON_TOOL),
        stream=True,
    )
    calls = [
        delta_call
        async for chunk in stream
        if chunk.choices and chunk.choices[0].delta.tool_calls
        for delta_call in chunk.choices[0].delta.tool_calls
    ]
    assert [c.function.name for c in calls] == [PYTHON_TOOL]

    models = await openai.models.list()
    assert [model.id for model in models.data] == ["fake-gpt"]

    logged = (await client.get("/fake/requests")).json()
    assert [entry["kind"] for entry in logged] == ["route", "chat", "agent"]
    assert (await client.delete("/fake/requests")).status_code == 204
    assert (await client.get("/fake/requests")).json() == []


@pytest.mark.asyncio
async def test_invalid_requests_get_openai_style_errors(http_client):
    _, client = http_client

    response = await client.post(
        "/v1/chat/completions",
        json={"messages": [_user("please simulate a provider error")]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Simulated provider failure"

    response = await client.post("/v1/chat/completions", content=b"{nope")
    assert response.status_code == 400
    assert "error" in response.json()

    response = await client.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 400


# ------------------------------------------------- the core against the fake


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def fake_server():
    """The fake model served over real HTTP in a background thread."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(FakeLLMSettings(slow_seconds=0, chunk_delay=0)),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            ws="none",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        threading.Event().wait(0.05)
    assert server.started, "fake LLM server did not start"
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def core_config(tmp_path: Path, fake_server: str):
    """Core configuration whose model is the fake server (no MCP servers)."""
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "config.toml").write_text(
        f"""
[llm]
model = "fake-gpt"
base_url = "{fake_server}"
api_key = "sk-fake-e2e-key"
max_tokens = 1024
temperature = 0.0

[browser]
headless = true

[runtime]
max_steps = 6
""",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("OPENMANUS_LLM", "OPENMANUS_BROWSER", "OPENMANUS_WORKSPACE")
        )
    }
    env["OPENMANUS_CONFIG_DIR"] = str(directory)
    env["OPENMANUS_WORKSPACE_ROOT"] = str(tmp_path / "workspace")
    with mock.patch.dict(os.environ, env, clear=True):
        config.reload()
        yield directory
    config.reload()


class Run:
    """A RunContext with recorded events and scripted human answers."""

    def __init__(self, workspace: Path, answers: List[str]):
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self.questions: List[str] = []
        self._answers = answers
        self.context = RunContext(
            run_id=uuid.uuid4().hex,
            workspace=workspace,
            emit_sink=lambda kind, data: self.events.append((kind, data)),
            ask_human=self._ask,
        )

    async def _ask(self, question: str) -> str:
        self.questions.append(question)
        return self._answers.pop(0)

    def of(self, kind: str) -> List[Dict[str, Any]]:
        return [data for event_kind, data in self.events if event_kind == kind]

    async def execute(self, request: str, mode: str = "auto") -> str:
        token = set_run(self.context)
        try:
            return await asyncio.wait_for(run_task(request, mode=mode), 60)
        finally:
            reset_run(token)


@pytest.fixture
def make_run(core_config, tmp_path: Path):
    def factory(*answers: str) -> Run:
        workspace = tmp_path / f"ws-{uuid.uuid4().hex[:6]}"
        workspace.mkdir()
        return Run(workspace, list(answers))

    return factory


@pytest.mark.asyncio
async def test_core_chat_route_streams_the_greeting(make_run):
    run = make_run()

    answer = await run.execute("Привет!")

    assert run.of("router.decision")[0]["mode"] == "chat"
    assert answer.startswith("Привет! 👋")
    assert "".join(e["content"] for e in run.of("answer.delta")) == answer
    assert run.of("usage")[0]["input_tokens"] > 0


@pytest.mark.asyncio
async def test_core_agent_route_writes_hello_txt(make_run):
    run = make_run()

    answer = await run.execute("Create the file hello.txt")

    assert run.of("router.decision")[0] == {
        "mode": "agent",
        "agent": "manus",
        "reason": "The task needs tools to work with files and code",
    }
    assert [e["name"] for e in run.of("tool.call")] == [PYTHON_TOOL, TERMINATE_TOOL]
    assert not any(e["error"] for e in run.of("tool.result"))
    assert run.of("agent.finished")[0]["reason"] == "terminated"
    hello = run.context.workspace / "hello.txt"
    assert hello.read_text(encoding="utf-8") == "Hello from OpenManus!\n"
    assert "- `hello.txt`" in answer
    assert run.of("final")[0]["content"] == answer


@pytest.mark.asyncio
async def test_core_ask_human_answer_is_written(make_run):
    run = make_run("Greetings from the e2e test")

    await run.execute("Ask me what to write into hello.txt")

    assert run.questions == ["What should I write into hello.txt?"]
    assert (run.context.workspace / "hello.txt").read_text(encoding="utf-8") == (
        "Greetings from the e2e test\n"
    )


@pytest.mark.asyncio
async def test_core_team_route_completes_a_two_step_plan(make_run):
    run = make_run()

    answer = await run.execute("Team: prepare hello.txt and a summary")

    assert run.of("router.decision")[0]["mode"] == "team"
    created = run.of("plan.created")[0]
    assert [step["agent"] for step in created["steps"]] == ["coder", "writer"]
    final_plan = run.of("plan.updated")[-1]
    assert [step["status"] for step in final_plan["steps"]] == ["completed"] * 2
    assert [e["status"] for e in run.of("plan.step_finished")] == ["completed"] * 2
    workspace = run.context.workspace
    assert (workspace / "hello.txt").exists()
    assert (
        (workspace / "summary.md").read_text(encoding="utf-8").startswith("# Summary")
    )
    assert "| 2 | Write summary.md describing hello.txt | writer |" in answer


@pytest.mark.asyncio
@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium missing")
async def test_core_browser_route_shows_the_page_screenshot(
    make_run, fake_server, monkeypatch
):
    monkeypatch.setenv("OPENMANUS_ALLOW_PRIVATE_NETWORK", "true")
    page = fake_server.removesuffix("/v1") + "/fake/page"
    run = make_run()

    await run.execute(f"Browse {page} and take a screenshot")

    assert run.of("router.decision")[0]["agent"] == "browser"
    (result,) = [e for e in run.of("tool.result") if e["name"] == "browser_use"]
    assert not result["error"]
    assert result["output"].endswith(f"Navigated to {page}")
    assert base64.b64decode(result["image_b64"])[:3] == b"\xff\xd8\xff"  # JPEG
    assert run.of("agent.finished")[0]["reason"] == "terminated"
