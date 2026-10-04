import json
import os
import stat
import tomllib
from pathlib import Path
from unittest import mock

import pytest

from app.agent.registry import get_agent_spec
from app.config import (
    BrowserSettings,
    config,
    load_mcp_servers_raw,
    load_raw_config,
    save_mcp_servers_raw,
    save_raw_config,
)
from app.llm import LLM


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_example_config_loads_without_daytona(tmp_path):
    empty_dir = tmp_path / "empty-config"
    empty_dir.mkdir()
    with mock.patch.dict(os.environ, {"OPENMANUS_CONFIG_DIR": str(empty_dir)}):
        config.reload()
        try:
            assert config.config_path.name == "config.example.toml"
            assert config.daytona.daytona_api_key is None
            assert config.llm_configured() is False  # placeholder API key
            available, reason = get_agent_spec("sandbox").is_available()
            assert available is False
            assert reason == "Daytona API key not configured"
        finally:
            config.reload()


def test_test_config_values(config_dir, tmp_path):
    assert config.llm["default"].model == "gpt-4o-mini"
    assert config.llm_configured() is True
    assert config.team.max_plan_steps == 5
    assert config.runtime.max_steps == 6
    assert config.workspace_root == (tmp_path / "workspace").resolve()
    assert config.env_overridden_keys() == {"workspace_root"}


def test_env_overrides_and_overridden_keys(config_dir):
    env = {
        "OPENMANUS_LLM_MODEL": "claude-sonnet-4-5",
        "OPENMANUS_LLM_API_KEY": "sk-env",
        "OPENMANUS_LLM_MAX_TOKENS": "not-a-number",
        "OPENMANUS_LLM_TEMPERATURE": "0.3",
        "OPENMANUS_LLM_SUPPORTS_IMAGES": "false",
        "OPENMANUS_LLM_VISION_MODEL": "gpt-4o",
        "OPENMANUS_LLM_VISION_SUPPORTS_IMAGES": "auto",
        "OPENMANUS_LLM_BASE_URL": "",  # empty values are ignored
        "OPENMANUS_BROWSER_HEADLESS": "false",
    }
    with mock.patch.dict(os.environ, env):
        config.reload()
        default, vision = config.llm["default"], config.llm["vision"]
        assert default.model == "claude-sonnet-4-5"
        assert default.api_key == "sk-env"
        assert default.max_tokens == 1024  # invalid override ignored
        assert default.temperature == 0.3
        assert default.supports_images is False
        assert default.base_url == "https://llm.test/v1"
        assert vision.model == "gpt-4o"
        assert vision.api_key == "sk-env"  # inherited from [llm]
        assert vision.supports_images is None
        assert config.browser_config.headless is False
        assert config.env_overridden_keys() == {
            "llm.model",
            "llm.api_key",
            "llm.temperature",
            "llm.supports_images",
            "llm.vision.model",
            "llm.vision.supports_images",
            "browser.headless",
            "workspace_root",
        }
    # The raw file never contains env values
    assert "claude-sonnet-4-5" not in json.dumps(load_raw_config())


def test_headless_defaults_to_true_without_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert BrowserSettings().headless is True


def test_llm_configured_rules(config_dir):
    raw = load_raw_config()
    raw["llm"]["api_key"] = "YOUR_API_KEY"
    save_raw_config(raw)
    config.reload()
    assert config.llm_configured() is False

    raw["llm"]["api_type"] = "ollama"
    save_raw_config(raw)
    config.reload()
    assert config.llm_configured() is True


def test_save_and_load_raw_config_roundtrip(config_dir):
    data = load_raw_config()
    data["llm"]["model"] = "gpt-4.1"
    data["llm"]["supports_images"] = None  # "auto" is stored by omission
    data["team"] = {"max_plan_steps": 3}
    save_raw_config(data)

    path = config_dir / "config.toml"
    assert _mode(path) == 0o600
    backup = config_dir / "config.toml.bak"
    assert backup.exists() and _mode(backup) == 0o600
    assert tomllib.loads(backup.read_text())["llm"]["model"] == "gpt-4o-mini"

    loaded = load_raw_config()
    assert loaded["llm"]["model"] == "gpt-4.1"
    assert "supports_images" not in loaded["llm"]
    assert loaded["team"] == {"max_plan_steps": 3}

    config.reload()
    assert config.llm["default"].model == "gpt-4.1"
    assert config.team.max_plan_steps == 3
    assert not list(config_dir.glob(".config.toml.*"))  # no temp files left


def test_save_raw_config_rejects_invalid_data(config_dir):
    before = (config_dir / "config.toml").read_text()
    data = load_raw_config()
    data["team"] = {"max_plan_steps": 0}
    with pytest.raises(ValueError):
        save_raw_config(data)
    assert (config_dir / "config.toml").read_text() == before


def test_mcp_servers_roundtrip_and_validation(config_dir):
    assert load_mcp_servers_raw() == {}
    servers = {
        "files": {"type": "stdio", "command": "python", "args": ["-m", "srv"]},
        "remote": {"type": "sse", "url": "http://127.0.0.1:9000/sse"},
    }
    save_mcp_servers_raw(servers)
    assert _mode(config_dir / "mcp.json") == 0o600
    assert load_mcp_servers_raw() == servers

    config.reload()
    assert set(config.mcp_config.servers) == {"files", "remote"}
    assert config.mcp_config.servers["files"].args == ["-m", "srv"]

    for invalid in (
        {"x": {"type": "sse"}},
        {"x": {"type": "stdio"}},
        {"x": {"type": "websocket", "url": "ws://x"}},
    ):
        with pytest.raises(ValueError):
            save_mcp_servers_raw(invalid)
    assert load_mcp_servers_raw() == servers


def test_invalid_mcp_entries_are_skipped(config_dir):
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"bad": {"url": "x"}, "ok": {"type": "sse", "url": "u"}}}
        )
    )
    config.reload()
    assert set(config.mcp_config.servers) == {"ok"}


def test_reload_resets_llm_instances(config_dir):
    first = LLM()
    assert LLM() is first
    data = load_raw_config()
    data["llm"]["model"] = "gpt-4.1-mini"
    save_raw_config(data)
    config.reload()
    second = LLM()
    assert second is not first
    assert second.model == "gpt-4.1-mini"
