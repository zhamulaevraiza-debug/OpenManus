"""Fixtures for the web backend tests.

Every test gets a private data directory, a private core configuration (so settings
writes never touch the repository) and an app whose ``task_runner`` is a scriptable
fake. The app's lifespan runs for the duration of the test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List
from unittest import mock

import httpx
import pytest
import pytest_asyncio
from web_helpers import (
    ADMIN,
    ADMIN_PASSWORD,
    TEST_API_KEY,
    FakeRunner,
    login,
    make_client,
    running_app,
)

from app.config import config
from app.web import security
from app.web.settings import WebSettings


TEST_CONFIG = f"""
[llm]
model = "gpt-4o-mini"
base_url = "https://llm.test/v1"
api_key = "{TEST_API_KEY}"
max_tokens = 1024
temperature = 0.0

[team]
max_plan_steps = 5

[runtime]
max_steps = 6
"""


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)


@pytest.fixture(autouse=True)
def config_dir(tmp_path: Path):
    """Private core configuration; OPENMANUS_LLM*/BROWSER/WORKSPACE env removed."""
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
    env["OPENMANUS_WORKSPACE_ROOT"] = str(tmp_path / "cli-workspace")
    with mock.patch.dict(os.environ, env, clear=True):
        config.reload()
        yield directory
    config.reload()


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "static"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(
        "<!doctype html><title>OpenManus</title>", encoding="utf-8"
    )
    (directory / "assets" / "app-123.js").write_text("console.log(1)")
    (directory / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    (directory / "manifest.webmanifest").write_text('{"name": "OpenManus"}')
    (directory / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return directory


@pytest.fixture
def web_settings(tmp_path: Path, static_dir: Path) -> WebSettings:
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{data_dir / 'openmanus.db'}",
        static_dir=static_dir,
        admin_username=ADMIN,
        admin_password=ADMIN_PASSWORD,
        trust_proxy=False,
    )


@pytest_asyncio.fixture
async def app(web_settings: WebSettings, fake_runner: FakeRunner):
    async with running_app(web_settings, fake_runner) as application:
        yield application


@pytest_asyncio.fixture
async def anon(app):
    async with make_client(app) as client:
        yield client


@pytest_asyncio.fixture
async def admin(app):
    async with make_client(app) as client:
        await login(client, ADMIN, ADMIN_PASSWORD)
        yield client


@pytest_asyncio.fixture
async def make_user(app, admin):
    """Factory: create a user via the admin API and return a logged-in client."""
    clients: List[httpx.AsyncClient] = []

    async def factory(username: str, password: str = "user-password-1", **extra):
        response = await admin.post(
            "/api/users",
            json={"username": username, "password": password, **extra},
        )
        assert response.status_code == 200, response.text
        client = make_client(app)
        clients.append(client)
        await login(client, username, password)
        return client

    yield factory
    for client in clients:
        await client.aclose()


@pytest_asyncio.fixture
async def user(make_user):
    return await make_user("alice")
