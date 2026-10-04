"""Admin settings (core config mapping, env locks, test-llm) and user management."""

import json
import os
import tomllib

import httpx
import pytest
import respx
from web_helpers import TEST_API_KEY, new_conversation, send_message, wait_for_run

from app.config import config


pytestmark = pytest.mark.asyncio

COMPLETIONS_URL = "https://llm.test/v1/chat/completions"


def completion(text: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    }


async def test_get_settings_masks_api_key(admin):
    response = await admin.get("/api/settings")
    assert response.status_code == 200
    settings = response.json()
    assert set(settings) == {
        "llm",
        "llm_vision",
        "search",
        "browser",
        "team",
        "runtime",
        "mcp_servers",
        "allow_registration",
        "locked_by_env",
    }
    llm = settings["llm"]
    assert llm == {
        "api_type": "",
        "model": "gpt-4o-mini",
        "base_url": "https://llm.test/v1",
        "api_key_set": True,
        "api_key_hint": "…" + TEST_API_KEY[-4:],
        "max_tokens": 1024,
        "temperature": 0.0,
        "api_version": "",
        "supports_images": None,
    }
    assert TEST_API_KEY not in response.text
    assert settings["llm_vision"] is None
    assert settings["team"] == {"max_plan_steps": 5}
    assert settings["runtime"] == {"max_steps": 6}
    assert settings["mcp_servers"] == {} and settings["allow_registration"] is False
    assert settings["locked_by_env"] == ["workspace_root"]
    assert settings["search"]["engine"] == "Google"


async def test_update_settings_writes_core_config(admin, config_dir):
    response = await admin.put(
        "/api/settings",
        json={
            "llm": {"model": "gpt-4.1", "temperature": 0.3, "supports_images": True},
            "llm_vision": {"model": "gpt-4o", "api_key": "sk-vision-abcdef123456"},
            "search": {"engine": "DuckDuckGo", "fallback_engines": ["Bing"]},
            "browser": {"headless": False},
            "team": {"max_plan_steps": 3},
            "runtime": {"max_steps": 12},
            "mcp_servers": {
                "files": {"type": "stdio", "command": "npx", "args": ["-y", "srv"]},
                "remote": {"type": "sse", "url": "https://mcp.example/sse"},
            },
        },
    )
    assert response.status_code == 200, response.text
    settings = response.json()
    assert settings["llm"]["model"] == "gpt-4.1"
    assert settings["llm"]["temperature"] == 0.3
    assert settings["llm"]["supports_images"] is True
    assert settings["llm"]["api_key_hint"] == "…" + TEST_API_KEY[-4:]  # kept
    assert settings["llm_vision"]["model"] == "gpt-4o"
    assert settings["llm_vision"]["api_key_hint"] == "…3456"
    assert settings["llm_vision"]["base_url"] == "https://llm.test/v1"  # inherited
    assert settings["search"]["engine"] == "DuckDuckGo"
    assert settings["browser"] == {"headless": False}
    assert settings["team"] == {"max_plan_steps": 3}
    assert settings["runtime"] == {"max_steps": 12}
    assert settings["mcp_servers"]["files"] == {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "srv"],
    }
    assert settings["mcp_servers"]["remote"] == {
        "type": "sse",
        "url": "https://mcp.example/sse",
    }

    raw = tomllib.loads((config_dir / "config.toml").read_text())
    assert raw["llm"]["model"] == "gpt-4.1" and raw["llm"]["api_key"] == TEST_API_KEY
    assert raw["llm"]["vision"]["api_key"] == "sk-vision-abcdef123456"
    assert raw["runtime"]["max_steps"] == 12
    assert os.stat(config_dir / "config.toml").st_mode & 0o777 == 0o600
    mcp = json.loads((config_dir / "mcp.json").read_text())
    assert set(mcp["mcpServers"]) == {"files", "remote"}
    assert config.runtime.max_steps == 12  # reloaded

    # Empty api_key keeps the key; a new one replaces it; null resets supports_images.
    kept = await admin.put(
        "/api/settings",
        json={"llm": {"api_key": "", "supports_images": None}, "llm_vision": None},
    )
    assert kept.json()["llm"]["api_key_hint"] == "…" + TEST_API_KEY[-4:]
    assert kept.json()["llm"]["supports_images"] is None
    assert kept.json()["llm_vision"] is None
    replaced = await admin.put(
        "/api/settings", json={"llm": {"api_key": "sk-new-key-000000009999"}}
    )
    assert replaced.json()["llm"]["api_key_hint"] == "…9999"
    assert "sk-new-key" not in replaced.text


async def test_invalid_settings_are_rejected(admin, config_dir):
    before = (config_dir / "config.toml").read_text()
    for body in (
        {"llm": {"temperature": 5}},
        {"llm": {"unknown": 1}},
        {"team": {"max_plan_steps": 0}},
        {"mcp_servers": {"x": {"type": "sse"}}},
        {"mcp_servers": {"x": {"type": "ws", "url": "u"}}},
    ):
        response = await admin.put("/api/settings", json=body)
        assert response.status_code == 422, body
    null_model = await admin.put("/api/settings", json={"llm": {"model": None}})
    assert null_model.status_code == 400
    assert (config_dir / "config.toml").read_text() == before


async def test_env_locked_keys_are_read_only(admin, monkeypatch):
    monkeypatch.setenv("OPENMANUS_LLM_MODEL", "env-model")
    monkeypatch.setenv("OPENMANUS_LLM_VISION_API_KEY", "sk-env-vision-key-7777")
    config.reload()
    settings = (await admin.get("/api/settings")).json()
    assert "llm.model" in settings["locked_by_env"]
    assert "llm_vision.api_key" in settings["locked_by_env"]
    assert settings["llm"]["model"] == "env-model"

    changed = await admin.put("/api/settings", json={"llm": {"model": "other"}})
    assert changed.status_code == 400 and "environment" in changed.json()["detail"]
    key = await admin.put(
        "/api/settings", json={"llm_vision": {"api_key": "sk-mine-123456789"}}
    )
    assert key.status_code == 400
    drop_vision = await admin.put("/api/settings", json={"llm_vision": None})
    assert drop_vision.status_code == 400
    same = await admin.put(
        "/api/settings", json={"llm": {"model": "env-model", "max_tokens": 2048}}
    )
    assert same.status_code == 200
    assert same.json()["llm"]["max_tokens"] == 2048


async def test_test_llm_uses_fresh_client(admin):
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMPLETIONS_URL).mock(
            return_value=httpx.Response(200, json=completion("OK"))
        )
        response = await admin.post("/api/settings/test-llm", json={"which": "llm"})
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True and "OK" in result["message"]
    assert isinstance(result["latency_ms"], int)
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {TEST_API_KEY}"
    assert json.loads(sent.content)["model"] == "gpt-4o-mini"

    # Settings changes take effect immediately (no cached client).
    await admin.put("/api/settings", json={"llm": {"model": "gpt-4.1-mini"}})
    with respx.mock() as router:
        route = router.post(COMPLETIONS_URL).mock(
            return_value=httpx.Response(
                401, json={"error": {"message": f"Bad key {TEST_API_KEY}"}}
            )
        )
        failed = (
            await admin.post("/api/settings/test-llm", json={"which": "llm"})
        ).json()
        assert json.loads(route.calls.last.request.content)["model"] == "gpt-4.1-mini"
    assert failed["ok"] is False and "AuthenticationError" in failed["message"]
    assert TEST_API_KEY not in failed["message"]

    vision = (
        await admin.post("/api/settings/test-llm", json={"which": "llm_vision"})
    ).json()
    assert vision["ok"] is False and "not configured" in vision["message"]
    bad = await admin.post("/api/settings/test-llm", json={"which": "other"})
    assert bad.status_code == 422


async def test_user_management_rules(admin, app, make_user):
    me = (await admin.get("/api/auth/me")).json()
    users = (await admin.get("/api/users")).json()
    assert [u["username"] for u in users] == ["admin"]

    created = await admin.post(
        "/api/users",
        json={"username": "Frank", "password": "frank-password", "is_admin": True},
    )
    assert created.status_code == 200
    frank = created.json()
    assert frank["username"] == "frank" and frank["is_admin"] is True
    duplicate = await admin.post(
        "/api/users", json={"username": "frank", "password": "frank-password"}
    )
    assert duplicate.status_code == 409
    invalid = await admin.post(
        "/api/users", json={"username": "a b", "password": "frank-password"}
    )
    assert invalid.status_code == 400
    short = await admin.post("/api/users", json={"username": "gina", "password": "x"})
    assert short.status_code == 422

    for body in ({"is_admin": False}, {"disabled": True}):
        response = await admin.patch(f"/api/users/{me['id']}", json=body)
        assert response.status_code == 400
    assert (await admin.delete(f"/api/users/{me['id']}")).status_code == 400
    assert (await admin.patch("/api/users/nope", json={})).status_code == 404

    demoted = await admin.patch(f"/api/users/{frank['id']}", json={"is_admin": False})
    assert demoted.status_code == 200 and demoted.json()["is_admin"] is False

    # Admin password reset revokes the user's sessions.
    henry = await make_user("henry")
    henry_id = (await henry.get("/api/auth/me")).json()["id"]
    reset = await admin.patch(
        f"/api/users/{henry_id}", json={"password": "reset-password-1"}
    )
    assert reset.status_code == 200
    assert (await henry.get("/api/auth/me")).status_code == 401


async def test_delete_user_removes_their_data(admin, app, make_user):
    ivan = await make_user("ivan")
    ivan_id = (await ivan.get("/api/auth/me")).json()["id"]
    conversation = await new_conversation(ivan)
    run_id = (await send_message(ivan, conversation["id"]))["run"]["id"]
    await wait_for_run(ivan, run_id)
    await ivan.post(
        f"/api/conversations/{conversation['id']}/files",
        files={"files": ("x.txt", b"x")},
    )
    settings = app.state.services.settings
    assert settings.user_workspaces(ivan_id).exists()

    response = await admin.delete(f"/api/users/{ivan_id}")
    assert response.status_code == 204
    assert not settings.user_workspaces(ivan_id).exists()
    assert (await ivan.get("/api/auth/me")).status_code == 401
    assert [u["username"] for u in (await admin.get("/api/users")).json()] == ["admin"]
    assert (await admin.get(f"/api/runs/{run_id}")).status_code == 404
