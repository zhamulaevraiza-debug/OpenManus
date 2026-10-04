import asyncio

import pytest

from app.agent.manus import Manus
from app.flow.runner import USER_SELECTED_REASON, run_task
from app.prompt.answer import CHAT_SYSTEM_PROMPT


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
HISTORY = [
    {"role": "user", "content": "Hi, I am Ann"},
    {"role": "assistant", "content": "Hello Ann!"},
    {"role": "system", "content": "ignored"},
]


def _texts(messages):
    return [(m.role, m.content) for m in messages]


@pytest.mark.asyncio
async def test_chat_mode_streams_one_answer_with_history(fake_llm, run_context, events):
    answer = await run_task("What is my name?", mode="chat", history=HISTORY)

    assert answer == fake_llm.answer
    assert events.types() == [
        "router.decision",
        "answer.delta",
        "answer.delta",
        "final",
        "usage",
    ]
    assert events.of("router.decision")[0] == {
        "mode": "chat",
        "agent": None,
        "reason": USER_SELECTED_REASON,
    }
    assert "".join(e["content"] for e in events.of("answer.delta")) == answer
    assert events.of("final")[0] == {"content": answer}
    assert events.of("usage")[0] == {"input_tokens": 20, "completion_tokens": 7}

    call = fake_llm.ask_calls[0]
    assert call["system"] == CHAT_SYSTEM_PROMPT
    assert _texts(call["messages"]) == [
        ("user", "Hi, I am Ann"),
        ("assistant", "Hello Ann!"),
        ("user", "What is my name?"),
    ]
    assert fake_llm.tool_calls == []  # no router call for an explicit mode


@pytest.mark.asyncio
async def test_agent_mode_runs_agent_and_writes_final_answer(
    fake_llm, run_context, events
):
    answer = await run_task("Compute the answer", mode="manus", history=HISTORY[:2])

    assert answer == fake_llm.answer
    assert events.types() == [
        "router.decision",
        "agent.started",
        "agent.step",
        "agent.thought",
        "tool.call",
        "tool.result",
        "agent.finished",
        "answer.delta",
        "answer.delta",
        "final",
        "usage",
    ]
    assert events.of("router.decision")[0]["agent"] == "manus"
    assert events.of("agent.started")[0] == {
        "agent": "manus",
        "title": "Manus",
        "max_steps": 6,
    }
    assert events.of("agent.finished")[0]["reason"] == "terminated"
    # 10/5 tokens for the agent step + 20/7 for the final answer
    assert events.of("usage")[0] == {"input_tokens": 30, "completion_tokens": 12}

    # The agent's memory was seeded with the history before the request
    agent_messages = fake_llm.tool_calls[0]["messages"]
    assert _texts(agent_messages[:3]) == [
        ("user", "Hi, I am Ann"),
        ("assistant", "Hello Ann!"),
        ("user", "Compute the answer"),
    ]
    # The final answer is written from the work record
    final_prompt = fake_llm.ask_calls[0]["messages"][0].content
    assert "the result is 42" in final_prompt
    assert "Compute the answer" in final_prompt
    assert "Hello Ann!" in final_prompt


@pytest.mark.asyncio
async def test_auto_mode_follows_the_router(fake_llm, run_context, events):
    fake_llm.route = {"mode": "agent", "agent": "writer", "reason": "needs a document"}
    await run_task("Write a short report")

    assert events.of("router.decision")[0] == {
        "mode": "agent",
        "agent": "writer",
        "reason": "needs a document",
    }
    assert events.of("agent.started")[0]["agent"] == "writer"
    assert "OpenManus Writer" in fake_llm.tool_calls[1]["system"]


@pytest.mark.asyncio
async def test_auto_mode_can_choose_chat(fake_llm, run_context, events):
    fake_llm.route = {"mode": "chat", "reason": "greeting"}
    await run_task("Hello!")
    assert events.of("router.decision")[0]["mode"] == "chat"
    assert "agent.started" not in events.types()


@pytest.mark.asyncio
async def test_router_errors_fall_back_to_manus(fake_llm, run_context, events):
    fake_llm.route = {"mode": "agent", "agent": "does_not_exist", "reason": "?"}
    await run_task("Do something")
    decision = events.of("router.decision")[0]
    assert (decision["mode"], decision["agent"]) == ("agent", "manus")
    assert events.of("agent.started")[0]["agent"] == "manus"


@pytest.mark.asyncio
async def test_team_mode_routes_steps_to_tagged_agents(fake_llm, run_context, events):
    fake_llm.plan = [
        "[Researcher] Find facts",
        "Step 2: [writer] Write the report",
        "[unknown_agent] Double-check",
        "[swe] Build a script",
    ]
    answer = await run_task("Research and write", mode="team", history=HISTORY[:2])

    assert answer == fake_llm.answer
    created = events.of("plan.created")[0]
    assert created["title"] == "Test plan"
    assert [(s["agent"], s["text"], s["status"]) for s in created["steps"]] == [
        ("researcher", "Find facts", "not_started"),
        ("writer", "Write the report", "not_started"),
        ("manus", "Double-check", "not_started"),
        ("coder", "Build a script", "not_started"),
    ]
    # Every step ran on a fresh agent of the tagged type
    assert [e["agent"] for e in events.of("agent.started")] == [
        "researcher",
        "writer",
        "manus",
        "coder",
    ]
    finished = events.of("plan.step_finished")
    assert [(e["index"], e["status"]) for e in finished] == [
        (i, "completed") for i in range(4)
    ]
    assert finished[0]["summary"] == "Done: the result is 42."
    final_plan = events.of("plan.updated")[-1]
    assert all(step["status"] == "completed" for step in final_plan["steps"])
    assert events.types()[-2:] == ["final", "usage"]

    # Later steps see earlier results; the final answer sees all of them
    step_prompts = [
        call["messages"][0].content
        for call in fake_llm.tool_calls
        if call["tools"] not in (["planning"],)
    ]
    assert "Done: the result is 42." in step_prompts[1]
    assert "Hello Ann!" in step_prompts[0]
    final_prompt = fake_llm.ask_calls[-1]["messages"][0].content
    assert final_prompt.count("Done: the result is 42.") == 4


@pytest.mark.asyncio
async def test_failed_team_step_is_blocked_and_flow_continues(
    fake_llm, run_context, events
):
    fake_llm.plan = ["[researcher] Find facts", "[writer] Write", "[manus] Check"]

    def agent(system, messages, tools):
        if "OpenManus Writer" in system:
            raise RuntimeError("writer crashed")
        return fake_llm.default_agent(system, messages, tools)

    fake_llm.agent = agent
    await run_task("Research and write", mode="team")

    finished = events.of("plan.step_finished")
    assert [e["status"] for e in finished] == ["completed", "blocked", "completed"]
    assert finished[1]["summary"] == "Error: writer crashed"
    assert len(events.of("agent.started")) == 3  # the failed step is not retried
    assert events.of("final")


@pytest.mark.asyncio
async def test_planner_without_tool_call_falls_back_to_single_step(
    fake_llm, run_context, events
):
    original = fake_llm.ask_tool

    async def no_plan(llm, messages, system_msgs=None, tools=None, **kwargs):
        if [t["function"]["name"] for t in tools or []] == ["planning"]:
            return fake_llm.reply("I would research first.")
        return await original(
            llm, messages, system_msgs=system_msgs, tools=tools, **kwargs
        )

    fake_llm.ask_tool = no_plan
    await run_task("Summarize the news", mode="team")
    steps = events.of("plan.created")[0]["steps"]
    assert [(s["agent"], s["text"]) for s in steps] == [("manus", "Summarize the news")]


@pytest.mark.asyncio
async def test_cancellation_cleans_up_and_propagates(
    fake_llm, run_context, events, monkeypatch
):
    started = asyncio.Event()

    async def blocking_agent(system, messages, tools):
        started.set()
        await asyncio.Event().wait()

    fake_llm.agent = blocking_agent
    cleaned = []
    original_cleanup = Manus.cleanup

    async def tracking_cleanup(self):
        cleaned.append(asyncio.current_task())
        await original_cleanup(self)

    monkeypatch.setattr(Manus, "cleanup", tracking_cleanup)

    task = asyncio.create_task(run_task("Long job", mode="manus"))
    await asyncio.wait_for(started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cleaned and all(t is task for t in cleaned)  # cleaned up in the run's task
    assert events.of("agent.finished")[-1]["reason"] == "cancelled"
    assert "final" not in events.types()
    assert events.types()[-1] == "usage"


@pytest.mark.asyncio
async def test_image_attachments_are_sent_to_vision_models(
    fake_llm, run_context, events, workspace
):
    (workspace / "uploads").mkdir()
    (workspace / "uploads" / "pic.png").write_bytes(PNG_BYTES)
    (workspace / "uploads" / "data.csv").write_text("a,b\n1,2\n")

    await run_task(
        "Describe the picture",
        mode="chat",
        attachments=["uploads/pic.png", "uploads/data.csv", "../outside.png"],
    )

    request = fake_llm.ask_calls[0]["messages"][-1]
    assert request.base64_image is not None
    assert (
        "uploads/pic.png" in request.content and "uploads/data.csv" in request.content
    )


@pytest.mark.asyncio
async def test_invalid_modes_raise_before_any_event(fake_llm, run_context, events):
    with pytest.raises(ValueError, match="Unknown mode"):
        await run_task("hi", mode="nonsense")
    with pytest.raises(ValueError, match="Daytona API key not configured"):
        await run_task("hi", mode="sandbox")
    assert events.events == []
