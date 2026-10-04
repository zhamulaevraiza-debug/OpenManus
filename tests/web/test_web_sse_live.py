"""Live Server-Sent Events over a real uvicorn server (streaming, pings, resume)."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from web_helpers import ADMIN, ADMIN_PASSWORD, login

from app.context import emit
from app.web.routes import runs as runs_routes


pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def live_server(app):
    """Serve an already started app on a free local port."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, lifespan="off", log_level="warning", ws="none"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


class SSEReader:
    """Incremental reader of an SSE response (one line iterator per response)."""

    def __init__(self, response: httpx.Response):
        self.lines = response.aiter_lines()
        self.comments = []

    async def until(self, event_type: str) -> list:
        """Read messages up to one of ``event_type`` (or the end of the stream)."""
        received, block = [], {}
        async for line in self.lines:
            if line.startswith(":"):
                self.comments.append(line)
                continue
            if line:
                name, _, value = line.partition(": ")
                block[name] = value
                continue
            if "data" in block:
                event = json.loads(block["data"])
                assert int(block["id"]) == event["seq"]
                assert block["event"] == event["type"]
                received.append(event)
                if event["type"] == event_type:
                    return received
            block = {}
        return received


async def test_live_stream_resume_and_ping(app, fake_runner, monkeypatch):
    monkeypatch.setattr(runs_routes, "PING_INTERVAL", 0.1)
    proceed = asyncio.Event()

    async def script(request, mode, history, attachments):
        emit("agent.thought", agent="manus", step=1, content="thinking")
        await proceed.wait()
        emit("final", content="done")
        return "done"

    fake_runner.script = script
    async with live_server(app) as base_url:
        async with httpx.AsyncClient(base_url=base_url, trust_env=False) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = (await client.post("/api/conversations", json={})).json()
            result = await client.post(
                f"/api/conversations/{conversation['id']}/messages",
                json={"content": "go"},
            )
            run_id = result.json()["run"]["id"]

            async with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
                assert stream.headers["content-type"].startswith("text/event-stream")
                reader = SSEReader(stream)
                first = await reader.until("agent.thought")
                assert [e["type"] for e in first] == [
                    "run.started",
                    "run.status",
                    "agent.thought",
                ]
                thought_seq = first[-1]["seq"]

                # A second subscriber resumes from the thought (Last-Event-ID).
                async with client.stream(
                    "GET",
                    f"/api/runs/{run_id}/events",
                    headers={"Last-Event-ID": str(thought_seq)},
                ) as resumed:
                    await asyncio.sleep(0.35)  # idle: pings arrive
                    proceed.set()
                    rest = await reader.until("run.finished")
                    resumed_events = await SSEReader(resumed).until("run.finished")
                assert [e["type"] for e in rest] == ["final", "run.finished"]
                assert resumed_events == rest
                assert rest[-1]["data"]["status"] == "completed"
                # The server closes the stream after run.finished.
                assert [line async for line in reader.lines] == []
            assert ": ping" in reader.comments

            # Polling fallback sees the same events.
            polled = (await client.get(f"/api/runs/{run_id}/events.json")).json()
            assert [e["seq"] for e in polled] == list(range(1, rest[-1]["seq"] + 1))


async def test_streams_close_on_shutdown_signal(app, fake_runner):
    proceed = asyncio.Event()

    async def script(request, mode, history, attachments):
        await proceed.wait()
        return "done"

    fake_runner.script = script
    async with live_server(app) as base_url:
        async with httpx.AsyncClient(base_url=base_url, trust_env=False) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = (await client.post("/api/conversations", json={})).json()
            result = await client.post(
                f"/api/conversations/{conversation['id']}/messages",
                json={"content": "go"},
            )
            run_id = result.json()["run"]["id"]
            async with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
                reader = SSEReader(stream)
                started = await reader.until("run.status")
                assert started[0]["type"] == "run.started"
                app.state.services.runs.begin_shutdown()
                remaining = await asyncio.wait_for(reader.until("run.finished"), 5)
                assert remaining == []  # stream ended without run.finished
            proceed.set()
