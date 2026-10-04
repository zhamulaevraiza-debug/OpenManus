"""Helpers shared by the web backend tests (imported by conftest and test modules)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from app.context import current_run, emit, get_workspace
from app.web.main import create_app
from app.web.settings import WebSettings


ADMIN = "admin"
ADMIN_PASSWORD = "admin-password-1"
BASE_URL = "http://testserver"
TEST_API_KEY = "sk-test-1234567890"
FINISHED = ("completed", "failed", "cancelled")

Script = Callable[..., Awaitable[str]]


class FakeRunner:
    """Stand-in for ``app.flow.runner.run_task`` recording its calls.

    By default it emits a router decision, a streamed answer and ``final``. Assign
    ``script`` (an async function with run_task's signature) for custom behaviour.
    """

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.script: Optional[Script] = None

    async def __call__(
        self,
        request: str,
        mode: str = "auto",
        history: Optional[list] = None,
        attachments: Optional[list] = None,
    ) -> str:
        run = current_run()
        self.calls.append(
            {
                "request": request,
                "mode": mode,
                "history": history,
                "attachments": attachments,
                "workspace": get_workspace(),
                "run_id": run.run_id if run else None,
            }
        )
        if self.script is not None:
            return await self.script(request, mode, history, attachments)
        emit("router.decision", mode="chat", agent=None, reason="test")
        emit("answer.delta", content="Echo")
        answer = f"Echo: {request}"
        emit("final", content=answer)
        return answer


@asynccontextmanager
async def running_app(settings: WebSettings, runner: FakeRunner):
    app = create_app(settings, task_runner=runner)
    async with app.router.lifespan_context(app):
        yield app


def make_client(application) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url=BASE_URL
    )


async def login(client: httpx.AsyncClient, username: str, password: str):
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response


async def new_conversation(client: httpx.AsyncClient, **body) -> dict:
    response = await client.post("/api/conversations", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def send_message(
    client: httpx.AsyncClient, conversation_id: str, content: str = "Hi", **extra
) -> dict:
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def wait_for_run(
    client: httpx.AsyncClient,
    run_id: str,
    statuses=FINISHED,
    timeout: float = 10,
) -> dict:
    """Poll a run until its status is one of ``statuses``; for finished statuses
    also until its ``run.finished`` event is stored."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in statuses:
            if run["status"] not in FINISHED:
                return run
            events = await run_events(client, run_id)
            if events and events[-1]["type"] == "run.finished":
                return (await client.get(f"/api/runs/{run_id}")).json()
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"run stayed {run['status']}")
        await asyncio.sleep(0.02)


async def run_events(client: httpx.AsyncClient, run_id: str, after: int = 0) -> list:
    response = await client.get(f"/api/runs/{run_id}/events.json?after={after}")
    assert response.status_code == 200, response.text
    return response.json()
