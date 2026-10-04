"""End-to-end run through the real ``app.flow.runner.run_task`` (chat mode) with
the OpenAI-compatible endpoint mocked by respx."""

import json

import httpx
import pytest
import respx
from web_helpers import (
    ADMIN,
    ADMIN_PASSWORD,
    TEST_API_KEY,
    login,
    make_client,
    new_conversation,
    run_events,
    send_message,
    wait_for_run,
)

from app.web.main import create_app


pytestmark = pytest.mark.asyncio

COMPLETIONS_URL = "https://llm.test/v1/chat/completions"


def stream_body(*chunks: str) -> bytes:
    lines = []
    for index, text in enumerate(chunks):
        lines.append(
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": "stop"
                            if index == len(chunks) - 1
                            else None,
                        }
                    ],
                }
            )
            + "\n\n"
        )
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


async def test_chat_run_with_real_runner(web_settings):
    app = create_app(web_settings)  # default task runner: app.flow.runner.run_task
    async with app.router.lifespan_context(app):
        async with make_client(app) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = await new_conversation(client, mode="chat")
            with respx.mock(assert_all_called=True) as router:
                route = router.post(COMPLETIONS_URL).mock(
                    return_value=httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        content=stream_body("Hello", ", ", "world!"),
                    )
                )
                first = await send_message(client, conversation["id"], "Say hello")
                run = await wait_for_run(client, first["run"]["id"])
                assert run["status"] == "completed", run["error"]

                request = json.loads(route.calls.last.request.content)
                assert route.calls.last.request.headers["authorization"] == (
                    f"Bearer {TEST_API_KEY}"
                )
                assert request["stream"] is True
                assert request["messages"][-1] == {
                    "role": "user",
                    "content": "Say hello",
                }

                events = await run_events(client, run["id"])
                types = [event["type"] for event in events]
                assert types[:3] == ["run.started", "run.status", "router.decision"]
                assert events[2]["data"]["mode"] == "chat"
                deltas = [
                    e["data"]["content"] for e in events if e["type"] == "answer.delta"
                ]
                assert "".join(deltas) == "Hello, world!"
                final = next(e for e in events if e["type"] == "final")
                assert final["data"]["content"] == "Hello, world!"
                assert "usage" in types and types[-1] == "run.finished"

                detail = (
                    await client.get(f"/api/conversations/{conversation['id']}")
                ).json()
                assert detail["messages"][-1]["content"] == "Hello, world!"

                # The second turn sends the first exchange as history.
                second = await send_message(client, conversation["id"], "Again")
                await wait_for_run(client, second["run"]["id"])
                messages = json.loads(route.calls.last.request.content)["messages"]
                assert [m["role"] for m in messages][-3:] == [
                    "user",
                    "assistant",
                    "user",
                ]
                assert messages[-3]["content"] == "Say hello"
                assert messages[-2]["content"] == "Hello, world!"
