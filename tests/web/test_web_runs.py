"""Run lifecycle: events, history, ask-human, cancel, timeout, limits, artifacts."""

import asyncio
import base64
import json

import pytest
from sqlalchemy import select
from web_helpers import (
    ADMIN,
    ADMIN_PASSWORD,
    login,
    make_client,
    new_conversation,
    run_events,
    running_app,
    send_message,
    wait_for_run,
)

from app.context import current_run, emit, get_workspace
from app.web import events as events_module
from app.web.db import Database
from app.web.models import Conversation, Message, Run, RunEvent, User


pytestmark = pytest.mark.asyncio

# 1x1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def parse_sse(text: str):
    """(id, event, data) tuples of an SSE body."""
    messages = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if "data" in fields:
            messages.append(
                (int(fields["id"]), fields["event"], json.loads(fields["data"]))
            )
    return messages


async def test_message_starts_run_and_records_events(user, fake_runner):
    conversation = await new_conversation(user)
    result = await send_message(user, conversation["id"], "Hello", mode="chat")
    message, run = result["message"], result["run"]
    assert message["role"] == "user" and message["content"] == "Hello"
    assert message["run_id"] == run["id"] and message["attachments"] == []
    assert run["status"] == "queued" and run["mode"] == "chat"
    assert set(run) == {
        "id",
        "conversation_id",
        "mode",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "error",
        "usage",
        "pending_question",
        "last_seq",
    }

    finished = await wait_for_run(user, run["id"])
    assert finished["status"] == "completed" and finished["error"] is None
    assert finished["started_at"] and finished["finished_at"]
    assert finished["usage"] == {"input_tokens": 0, "completion_tokens": 0}

    events = await run_events(user, run["id"])
    types = [event["type"] for event in events]
    assert types == [
        "run.started",
        "run.status",
        "router.decision",
        "answer.delta",
        "final",
        "run.finished",
    ]
    assert [event["seq"] for event in events] == list(range(1, 7))
    assert all(event["run_id"] == run["id"] for event in events)
    assert all(event["ts"].endswith("Z") for event in events)
    assert events[0]["data"] == {"mode": "chat"}
    done = events[-1]["data"]
    assert done["status"] == "completed" and done["duration_ms"] >= 0
    assert finished["last_seq"] == 6
    assert await run_events(user, run["id"], after=4) == events[4:]

    detail = (await user.get(f"/api/conversations/{conversation['id']}")).json()
    assistant = detail["messages"][-1]
    assert assistant["role"] == "assistant" and assistant["content"] == "Echo: Hello"
    assert assistant["run_id"] == run["id"]
    assert detail["conversation"]["mode"] == "chat"  # mode remembered

    call = fake_runner.calls[0]
    assert call["mode"] == "chat" and call["history"] == []
    me = (await user.get("/api/auth/me")).json()
    assert call["workspace"].name == conversation["id"]
    assert call["workspace"].parent.name == me["id"]
    assert call["run_id"] == run["id"]


async def test_history_is_passed_to_later_runs(user, fake_runner):
    conversation = await new_conversation(user)
    first = await send_message(user, conversation["id"], "First question")
    await wait_for_run(user, first["run"]["id"])
    second = await send_message(user, conversation["id"], "Second question")
    await wait_for_run(user, second["run"]["id"])
    assert fake_runner.calls[1]["history"] == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "Echo: First question"},
    ]


async def test_sse_replays_finished_run_and_honours_last_event_id(user):
    conversation = await new_conversation(user)
    result = await send_message(user, conversation["id"], "Hi")
    run_id = result["run"]["id"]
    await wait_for_run(user, run_id)

    response = await user.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    messages = parse_sse(response.text)
    assert [seq for seq, _, _ in messages] == list(range(1, 7))
    assert messages[-1][1] == "run.finished"
    assert all(
        data["seq"] == seq and data["type"] == kind for seq, kind, data in messages
    )

    resumed = await user.get(
        f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "4"}
    )
    assert [seq for seq, _, _ in parse_sse(resumed.text)] == [5, 6]
    after = await user.get(f"/api/runs/{run_id}/events?after=5")
    assert [seq for seq, _, _ in parse_sse(after.text)] == [6]


async def test_ask_human_round_trip(user, fake_runner):
    async def script(request, mode, history, attachments):
        answer = await current_run().ask_human("Which city?")
        emit("final", content=f"Going to {answer}")
        return f"Going to {answer}"

    fake_runner.script = script
    conversation = await new_conversation(user)
    run_id = (await send_message(user, conversation["id"], "Plan a trip"))["run"]["id"]

    waiting = await wait_for_run(user, run_id, statuses=("waiting_input",))
    question = waiting["pending_question"]
    assert question["question"] == "Which city?" and question["question_id"]

    wrong = await user.post(
        f"/api/runs/{run_id}/answer", json={"question_id": "nope", "answer": "x"}
    )
    assert wrong.status_code == 404
    response = await user.post(
        f"/api/runs/{run_id}/answer",
        json={"question_id": question["question_id"], "answer": "Kazan"},
    )
    assert response.status_code == 204
    again = await user.post(
        f"/api/runs/{run_id}/answer",
        json={"question_id": question["question_id"], "answer": "Kazan"},
    )
    assert again.status_code == 404

    finished = await wait_for_run(user, run_id)
    assert finished["status"] == "completed" and finished["pending_question"] is None
    events = await run_events(user, run_id)
    types = [event["type"] for event in events]
    asked = types.index("human.question")
    assert types[asked : asked + 4] == [
        "human.question",
        "run.status",
        "human.answer",
        "run.status",
    ]
    assert events[asked]["data"] == question
    assert events[asked + 2]["data"] == {
        "question_id": question["question_id"],
        "answer": "Kazan",
    }
    assert events[asked + 1]["data"] == {"status": "waiting_input"}
    detail = (await user.get(f"/api/conversations/{conversation['id']}")).json()
    assert detail["messages"][-1]["content"] == "Going to Kazan"


async def test_unanswered_question_times_out(web_settings, fake_runner):
    async def script(request, mode, history, attachments):
        return await current_run().ask_human("Anyone there?")

    fake_runner.script = script
    settings = web_settings.with_overrides(human_input_timeout=0.2)
    async with running_app(settings, fake_runner) as app:
        async with make_client(app) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = await new_conversation(client)
            run_id = (await send_message(client, conversation["id"]))["run"]["id"]
            finished = await wait_for_run(client, run_id)
            assert finished["status"] == "completed"
            events = await run_events(client, run_id)
            answer = next(e for e in events if e["type"] == "human.answer")
            assert answer["data"]["answer"] == "The user did not answer in time."


async def test_cancel_running_run(user, fake_runner):
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def script(request, mode, history, attachments):
        emit("agent.started", agent="manus", title="Manus", max_steps=5)
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cleaned_up.set()
        return "never"

    fake_runner.script = script
    conversation = await new_conversation(user)
    run_id = (await send_message(user, conversation["id"]))["run"]["id"]
    await asyncio.wait_for(started.wait(), 5)
    listed = (await user.get("/api/conversations")).json()[0]
    assert listed["active_run_id"] == run_id

    response = await user.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert cleaned_up.is_set()
    finished = await wait_for_run(user, run_id)
    assert finished["status"] == "cancelled"
    events = await run_events(user, run_id)
    assert events[-1]["type"] == "run.finished"
    assert events[-1]["data"]["status"] == "cancelled"
    detail = (await user.get(f"/api/conversations/{conversation['id']}")).json()
    assert detail["messages"][-1]["content"] == "⏹"
    assert detail["conversation"]["active_run_id"] is None
    # Cancelling a finished run is a no-op.
    again = await user.post(f"/api/runs/{run_id}/cancel")
    assert again.status_code == 200 and again.json()["status"] == "cancelled"


async def test_run_timeout_marks_failed(web_settings, fake_runner):
    async def script(request, mode, history, attachments):
        await asyncio.sleep(30)
        return "late"

    fake_runner.script = script
    settings = web_settings.with_overrides(run_timeout=0.3)
    async with running_app(settings, fake_runner) as app:
        async with make_client(app) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = await new_conversation(client)
            run_id = (await send_message(client, conversation["id"]))["run"]["id"]
            finished = await wait_for_run(client, run_id)
            assert finished["status"] == "failed" and finished["error"] == "timeout"
            detail = (
                await client.get(f"/api/conversations/{conversation['id']}")
            ).json()
            assert detail["messages"][-1]["content"].startswith("⚠️")


async def test_runner_errors_mark_run_failed(user, fake_runner):
    async def unknown_mode(request, mode, history, attachments):
        raise ValueError("Agent 'Sandbox' is not available: Daytona API key not set")

    fake_runner.script = unknown_mode
    conversation = await new_conversation(user)
    run_id = (await send_message(user, conversation["id"]))["run"]["id"]
    finished = await wait_for_run(user, run_id)
    assert finished["status"] == "failed"
    assert finished["error"].startswith("Agent 'Sandbox' is not available")

    async def crash(request, mode, history, attachments):
        raise RuntimeError("boom")

    fake_runner.script = crash
    run_id = (await send_message(user, conversation["id"]))["run"]["id"]
    finished = await wait_for_run(user, run_id)
    assert finished["status"] == "failed" and finished["error"] == "RuntimeError: boom"
    events = await run_events(user, run_id)
    assert events[-1]["data"]["error"] == "RuntimeError: boom"

    # Answers of failed runs are not part of the history of later runs.
    fake_runner.script = None
    run_id = (await send_message(user, conversation["id"], "third"))["run"]["id"]
    await wait_for_run(user, run_id)
    history = fake_runner.calls[-1]["history"]
    assert all(turn["role"] == "user" for turn in history)


async def test_invalid_or_unavailable_mode_is_rejected(user):
    conversation = await new_conversation(user)
    response = await user.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "hi", "mode": "no-such-agent"},
    )
    assert response.status_code == 400
    sandbox = await user.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "hi", "mode": "sandbox"},
    )
    assert sandbox.status_code == 400
    assert "not available" in sandbox.json()["detail"]
    for content in ("", "x" * 20001):
        bad = await user.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": content},
        )
        assert bad.status_code == 422


async def test_retry_reruns_last_user_message(user, fake_runner):
    async def crash(request, mode, history, attachments):
        raise RuntimeError("flaky")

    fake_runner.script = crash
    conversation = await new_conversation(user)
    first = await send_message(user, conversation["id"], "Do the thing")
    await wait_for_run(user, first["run"]["id"])

    fake_runner.script = None
    response = await user.post(f"/api/conversations/{conversation['id']}/retry")
    assert response.status_code == 200
    retried = response.json()
    assert retried["message"]["id"] == first["message"]["id"]
    assert retried["message"]["run_id"] == retried["run"]["id"]
    assert retried["run"]["id"] != first["run"]["id"]
    finished = await wait_for_run(user, retried["run"]["id"])
    assert finished["status"] == "completed"
    assert fake_runner.calls[-1]["request"] == "Do the thing"
    assert fake_runner.calls[-1]["history"] == []

    empty = await new_conversation(user)
    nothing = await user.post(f"/api/conversations/{empty['id']}/retry")
    assert nothing.status_code == 404


async def test_concurrency_limits(web_settings, fake_runner):
    release = asyncio.Event()

    async def script(request, mode, history, attachments):
        await release.wait()
        return "done"

    fake_runner.script = script
    settings = web_settings.with_overrides(
        max_concurrent_runs=1, max_runs_per_user=2, max_queued_runs=1
    )
    async with running_app(settings, fake_runner) as app:
        async with make_client(app) as admin, make_client(app) as bob:
            await login(admin, ADMIN, ADMIN_PASSWORD)
            created = await admin.post(
                "/api/users", json={"username": "bob", "password": "bob-password"}
            )
            assert created.status_code == 200
            await login(bob, "bob", "bob-password")

            first = await new_conversation(admin)
            run1 = (await send_message(admin, first["id"]))["run"]["id"]
            await wait_for_run(admin, run1, statuses=("running",))
            busy = await admin.post(
                f"/api/conversations/{first['id']}/messages", json={"content": "x"}
            )
            assert busy.status_code == 409
            assert (
                await admin.post(f"/api/conversations/{first['id']}/retry")
            ).status_code == 409

            second = await new_conversation(admin)
            run2 = (await send_message(admin, second["id"]))["run"]["id"]
            await asyncio.sleep(0.1)
            assert (await admin.get(f"/api/runs/{run2}")).json()["status"] == "queued"

            third = await new_conversation(admin)
            per_user = await admin.post(
                f"/api/conversations/{third['id']}/messages", json={"content": "x"}
            )
            assert per_user.status_code == 429

            bobs = await new_conversation(bob)
            server_full = await bob.post(
                f"/api/conversations/{bobs['id']}/messages", json={"content": "x"}
            )
            assert server_full.status_code == 429
            status = (await admin.get("/api/status")).json()
            assert status["active_runs"] == 2 and status["max_concurrent_runs"] == 1

            release.set()
            assert (await wait_for_run(admin, run1))["status"] == "completed"
            assert (await wait_for_run(admin, run2))["status"] == "completed"
            assert (await send_message(bob, bobs["id"]))["run"]["status"] == "queued"


async def test_screenshots_become_artifacts(user, make_user, fake_runner):
    async def script(request, mode, history, attachments):
        emit(
            "tool.result",
            agent="browser",
            step=1,
            call_id="c1",
            name="browser_use",
            output="Navigated",
            error=False,
            image_b64=base64.b64encode(PNG).decode(),
        )
        emit(
            "tool.result",
            agent="browser",
            step=2,
            call_id="c2",
            name="browser_use",
            output="bad image",
            error=False,
            image_b64="bm90IGFuIGltYWdl",
        )
        return "ok"

    fake_runner.script = script
    conversation = await new_conversation(user)
    run_id = (await send_message(user, conversation["id"]))["run"]["id"]
    await wait_for_run(user, run_id)
    results = [e for e in await run_events(user, run_id) if e["type"] == "tool.result"]
    first, second = results
    assert "image_b64" not in first["data"] and "image_b64" not in second["data"]
    assert first["data"]["image_url"] == (
        f"/api/runs/{run_id}/artifacts/{first['seq']:06d}.png"
    )
    assert "image_url" not in second["data"]  # not a PNG/JPEG

    image = await user.get(first["data"]["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png" and image.content == PNG
    assert (await user.get(f"/api/runs/{run_id}/artifacts/../x.png")).status_code == 404
    assert (
        await user.get(f"/api/runs/{run_id}/artifacts/999999.png")
    ).status_code == 404
    other = await make_user("eve")
    assert (await other.get(first["data"]["image_url"])).status_code == 404


async def test_workspace_changes_are_reported(user, fake_runner):
    async def script(request, mode, history, attachments):
        (get_workspace() / "report.md").write_text("# Report")
        emit(
            "tool.result",
            agent="writer",
            step=1,
            call_id="c1",
            name="editor",
            output="saved",
            error=False,
        )
        emit(
            "tool.result",
            agent="writer",
            step=2,
            call_id="c2",
            name="noop",
            output="nothing",
            error=False,
        )
        await asyncio.sleep(0.3)  # events are processed asynchronously
        (get_workspace() / "late.txt").write_text("after the last tool")
        return "done"

    fake_runner.script = script
    conversation = await new_conversation(user)
    run_id = (await send_message(user, conversation["id"]))["run"]["id"]
    await wait_for_run(user, run_id)
    events = await run_events(user, run_id)
    changes = [e for e in events if e["type"] == "workspace.changed"]
    assert [c["data"]["paths"] for c in changes] == [["report.md"], ["late.txt"]]
    first_result = next(e for e in events if e["type"] == "tool.result")
    assert changes[0]["seq"] == first_result["seq"] + 1
    assert events[-1]["type"] == "run.finished"


async def test_event_cap_keeps_essential_events(user, fake_runner, monkeypatch):
    monkeypatch.setattr(events_module, "MAX_PERSISTED_EVENTS", 5)

    async def script(request, mode, history, attachments):
        for index in range(20):
            emit("answer.delta", content=str(index))
        emit("plan.created", plan_id="p", title="t", steps=[])
        emit("final", content="final answer")
        return "final answer"

    fake_runner.script = script
    conversation = await new_conversation(user)
    run_id = (await send_message(user, conversation["id"]))["run"]["id"]
    finished = await wait_for_run(user, run_id)
    events = await run_events(user, run_id)
    types = [e["type"] for e in events]
    assert types.count("answer.delta") == 3  # run.started + run.status + 3 = cap 5
    assert types[-3:] == ["plan.created", "final", "run.finished"]
    assert finished["last_seq"] == events[-1]["seq"] == 25


async def test_startup_marks_interrupted_runs_failed(web_settings, fake_runner):
    async with running_app(web_settings, fake_runner):
        pass  # creates the schema and the admin
    db = Database(web_settings.database_url)
    try:
        async with db.session() as session:
            admin_user = (await session.scalars(select(User))).first()
            conversation = Conversation(user_id=admin_user.id, title="t")
            session.add(conversation)
            await session.flush()
            run = Run(
                conversation_id=conversation.id,
                user_id=admin_user.id,
                mode="auto",
                status="waiting_input",
                pending_question={"question_id": "q", "question": "?"},
                last_seq=2,
            )
            session.add(run)
            await session.flush()
            for seq in (1, 2):
                session.add(
                    RunEvent(
                        run_id=run.id,
                        seq=seq,
                        type="run.status",
                        ts=run.created_at,
                        data={"status": "running"},
                    )
                )
            await session.commit()
            run_id, conversation_id = run.id, conversation.id
    finally:
        await db.dispose()

    async with running_app(web_settings, fake_runner) as app:
        async with make_client(app) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            recovered = (await client.get(f"/api/runs/{run_id}")).json()
            assert recovered["status"] == "failed"
            assert recovered["error"] == "Server restarted"
            assert recovered["pending_question"] is None
            assert recovered["finished_at"] is not None and recovered["last_seq"] == 3
            stream = await client.get(f"/api/runs/{run_id}/events?after=2")
            ((seq, kind, data),) = parse_sse(stream.text)
            assert (seq, kind) == (3, "run.finished")
            assert data["data"]["status"] == "failed"
            detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
            assert detail["messages"][-1]["role"] == "assistant"
            # A new run in the same conversation is possible again.
            result = await send_message(client, conversation_id, "again")
            assert (await wait_for_run(client, result["run"]["id"]))[
                "status"
            ] == "completed"


async def test_shutdown_cancels_active_runs(web_settings, fake_runner):
    started = asyncio.Event()

    async def script(request, mode, history, attachments):
        started.set()
        await asyncio.sleep(60)
        return "never"

    fake_runner.script = script
    async with running_app(web_settings, fake_runner) as app:
        async with make_client(app) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = await new_conversation(client)
            run_id = (await send_message(client, conversation["id"]))["run"]["id"]
            await asyncio.wait_for(started.wait(), 5)
    # The lifespan has ended: the run was stopped and recorded.
    db = Database(web_settings.database_url)
    try:
        async with db.session() as session:
            run = await session.get(Run, run_id)
            assert run.status == "failed" and run.error == "Server shut down"
            replies = (
                await session.scalars(
                    select(Message).where(
                        Message.run_id == run_id, Message.role == "assistant"
                    )
                )
            ).all()
            assert [reply.content for reply in replies] == ["⚠️ Server shut down"]
    finally:
        await db.dispose()
