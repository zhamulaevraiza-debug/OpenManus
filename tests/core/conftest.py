"""Fixtures for core tests: isolated configuration, run context and a scripted LLM."""

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock

import pytest
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from app.config import config
from app.context import RunContext, reset_run, set_run
from app.llm import LLM


TEST_CONFIG = """
[llm]
model = "gpt-4o-mini"
base_url = "https://llm.test/v1"
api_key = "test-key"
max_tokens = 1024
temperature = 0.0

[team]
max_plan_steps = 5

[runtime]
max_steps = 6
"""


@pytest.fixture
def config_dir(tmp_path: Path):
    """A private config directory (and workspace root) for the duration of a test.

    ``OPENMANUS_*`` environment overrides of the developer machine are removed.
    """
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "config.toml").write_text(TEST_CONFIG, encoding="utf-8")
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


class EventRecorder:
    """Collects events emitted during a run."""

    def __init__(self):
        self.events: List[Tuple[str, Dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))

    def types(self) -> List[str]:
        return [event_type for event_type, _ in self.events]

    def of(self, event_type: str) -> List[Dict[str, Any]]:
        return [data for kind, data in self.events if kind == event_type]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "run-workspace"
    path.mkdir()
    return path


@pytest.fixture
def events() -> EventRecorder:
    return EventRecorder()


@pytest.fixture
def run_context(config_dir, workspace: Path, events: EventRecorder):
    """An active RunContext whose events are recorded in ``events``."""
    ctx = RunContext(run_id=uuid.uuid4().hex, workspace=workspace, emit_sink=events)
    token = set_run(ctx)
    yield ctx
    reset_run(token)


def tool_call(name: str, **arguments) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id=f"call_{uuid.uuid4().hex[:8]}",
        type="function",
        function=Function(name=name, arguments=json.dumps(arguments)),
    )


def reply(content: Optional[str] = None, *calls) -> ChatCompletionMessage:
    return ChatCompletionMessage(
        role="assistant", content=content, tool_calls=list(calls) or None
    )


def _tool_names(tools: Optional[List[dict]]) -> List[str]:
    return [tool["function"]["name"] for tool in tools or []]


def _system_text(system_msgs) -> str:
    return "\n".join(
        (m.content if hasattr(m, "content") else m.get("content", "")) or ""
        for m in system_msgs or []
    )


class FakeLLM:
    """Scripted replacement for ``LLM.ask`` / ``LLM.ask_tool`` (patched on the class).

    * ``route``: arguments of the router's ``route`` call.
    * ``plan``: steps of the planner's ``planning`` call.
    * ``agent``: ``callable(system_prompt, messages, tool_names) -> ChatCompletionMessage``
      for agent steps; the default immediately terminates with a summary.
    * ``answer``: text returned (and streamed in two chunks) by ``ask``.
    """

    def __init__(self):
        self.route: Dict[str, Any] = {
            "mode": "agent",
            "agent": "manus",
            "reason": "test",
        }
        self.plan: List[str] = ["[manus] Do the task"]
        self.plan_title = "Test plan"
        self.agent: Callable[..., ChatCompletionMessage] = self.default_agent
        self.answer = "Final **answer**"
        self.ask_calls: List[dict] = []
        self.tool_calls: List[dict] = []

    tool_call = staticmethod(tool_call)
    reply = staticmethod(reply)

    @staticmethod
    def default_agent(system_prompt, messages, tool_names) -> ChatCompletionMessage:
        return reply(
            "Done: the result is 42.", tool_call("terminate", status="success")
        )

    async def ask_tool(
        self, llm: LLM, messages, system_msgs=None, tools=None, **kwargs
    ):
        names = _tool_names(tools)
        self.tool_calls.append(
            {
                "messages": list(messages),
                "system": _system_text(system_msgs),
                "tools": names,
                **kwargs,
            }
        )
        llm.update_token_count(10, 5)
        if names == ["route"]:
            return reply(None, tool_call("route", **self.route))
        if names == ["planning"]:
            return reply(
                None,
                tool_call(
                    "planning", command="create", title=self.plan_title, steps=self.plan
                ),
            )
        result = self.agent(_system_text(system_msgs), list(messages), names)
        if hasattr(result, "__await__"):
            result = await result
        return result

    async def ask(
        self,
        llm: LLM,
        messages,
        system_msgs=None,
        stream=True,
        temperature=None,
        on_delta=None,
    ):
        self.ask_calls.append(
            {
                "messages": list(messages),
                "system": _system_text(system_msgs),
                "on_delta": on_delta,
            }
        )
        llm.update_token_count(20, 7)
        if on_delta is not None:
            half = len(self.answer) // 2
            on_delta(self.answer[:half])
            on_delta(self.answer[half:])
        return self.answer


@pytest.fixture
def fake_llm(monkeypatch, config_dir) -> FakeLLM:
    fake = FakeLLM()

    async def ask_tool(self, messages, system_msgs=None, **kwargs):
        return await fake.ask_tool(self, messages, system_msgs=system_msgs, **kwargs)

    async def ask(self, messages, system_msgs=None, **kwargs):
        return await fake.ask(self, messages, system_msgs=system_msgs, **kwargs)

    monkeypatch.setattr(LLM, "ask_tool", ask_tool)
    monkeypatch.setattr(LLM, "ask", ask)
    return fake
