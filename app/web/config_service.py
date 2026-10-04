"""Admin settings: a view of the core configuration and partial updates to it.

Values shown are the effective ones (environment overrides applied). Updates are
written to ``config.toml`` / ``mcp.json`` through the core helpers, followed by a
configuration reload. Keys overridden by environment variables are read-only.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import time
from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.config import (
    LLMSettings,
    SearchSettings,
    config,
    load_mcp_servers_raw,
    load_raw_config,
    save_mcp_servers_raw,
    save_raw_config,
)
from app.utils.text import truncate


TEST_LLM_TIMEOUT = 30.0
TEST_LLM_PROMPT = "Reply with the single word: OK"
LLM_FIELDS = (
    "api_type",
    "model",
    "base_url",
    "max_tokens",
    "temperature",
    "api_version",
    "supports_images",
)
_PLACEHOLDER_KEYS = {"your_api_key", "your-api-key", "azure api key", "sk-..."}


class SettingsError(ValueError):
    """Invalid settings update (user-facing message, HTTP 400)."""


# ------------------------------------------------------------------ requests


class _Update(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMUpdate(_Update):
    api_type: Optional[str] = Field(None, max_length=32, pattern=r"^[A-Za-z0-9_\-]*$")
    model: Optional[str] = Field(None, max_length=200)
    base_url: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=1000)
    max_tokens: Optional[int] = Field(None, ge=1, le=10_000_000)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    api_version: Optional[str] = Field(None, max_length=64)
    supports_images: Optional[bool] = None


class SearchUpdate(_Update):
    engine: Optional[str] = Field(None, max_length=32)
    fallback_engines: Optional[List[str]] = Field(None, max_length=10)
    lang: Optional[str] = Field(None, max_length=16)
    country: Optional[str] = Field(None, max_length=16)


class BrowserUpdate(_Update):
    headless: Optional[bool] = None


class TeamUpdate(_Update):
    max_plan_steps: Optional[int] = Field(None, ge=1, le=50)


class RuntimeUpdate(_Update):
    max_steps: Optional[int] = Field(None, ge=1, le=500)


class MCPServer(_Update):
    type: Literal["sse", "stdio"]
    url: Optional[str] = Field(None, max_length=2000)
    command: Optional[str] = Field(None, max_length=1000)
    args: List[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _check_target(self) -> "MCPServer":
        if self.type == "sse" and not (self.url or "").strip():
            raise ValueError("url is required for sse servers")
        if self.type == "stdio" and not (self.command or "").strip():
            raise ValueError("command is required for stdio servers")
        return self


class SettingsUpdate(_Update):
    llm: Optional[LLMUpdate] = None
    llm_vision: Optional[LLMUpdate] = None
    search: Optional[SearchUpdate] = None
    browser: Optional[BrowserUpdate] = None
    team: Optional[TeamUpdate] = None
    runtime: Optional[RuntimeUpdate] = None
    mcp_servers: Optional[Dict[str, MCPServer]] = None
    allow_registration: Optional[bool] = None


# ---------------------------------------------------------------------- view


def _is_placeholder(api_key: str) -> bool:
    lowered = api_key.strip().lower()
    return lowered in _PLACEHOLDER_KEYS or ("your" in lowered and "key" in lowered)


def _key_hint(api_key: str) -> Optional[str]:
    key = api_key.strip()
    if not key or _is_placeholder(key):
        return None
    return "…" + key[-4:] if len(key) >= 12 else "…"


def _llm_view(settings: LLMSettings) -> Dict[str, Any]:
    key = settings.api_key or ""
    return {
        "api_type": settings.api_type or "",
        "model": settings.model or "",
        "base_url": settings.base_url or "",
        "api_key_set": bool(key.strip()) and not _is_placeholder(key),
        "api_key_hint": _key_hint(key),
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
        "api_version": settings.api_version or "",
        "supports_images": settings.supports_images,
    }


def _ui_key(core_key: str) -> str:
    """Core dotted key (``llm.vision.model``) in Settings JSON terms
    (``llm_vision.model``)."""
    if core_key.startswith("llm.vision."):
        return "llm_vision." + core_key[len("llm.vision.") :]
    return core_key


def _mcp_view(servers: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    view = {}
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        entry: Dict[str, Any] = {"type": server.get("type", "stdio")}
        if server.get("url"):
            entry["url"] = server["url"]
        if server.get("command"):
            entry["command"] = server["command"]
        if server.get("args"):
            entry["args"] = list(server["args"])
        view[name] = entry
    return view


def settings_view(allow_registration: bool) -> Dict[str, Any]:
    """The Settings JSON (sync: reads configuration files)."""
    llm = config.llm.get("default")
    vision = config.llm.get("vision")
    search = config.search_config or SearchSettings()
    browser = config.browser_config
    return {
        "llm": _llm_view(llm),
        "llm_vision": _llm_view(vision) if vision is not None else None,
        "search": {
            "engine": search.engine,
            "fallback_engines": list(search.fallback_engines),
            "lang": search.lang,
            "country": search.country,
        },
        "browser": {"headless": bool(browser.headless) if browser else True},
        "team": {"max_plan_steps": config.team.max_plan_steps},
        "runtime": {"max_steps": config.runtime.max_steps},
        "mcp_servers": _mcp_view(load_mcp_servers_raw()),
        "allow_registration": bool(allow_registration),
        "locked_by_env": sorted(_ui_key(key) for key in config.env_overridden_keys()),
    }


# -------------------------------------------------------------------- update


def _check_locked(locked: Set[str], core_key: str, value: Any, current: Any) -> bool:
    """Whether to write ``value``; raises for a changed env-locked key."""
    if core_key not in locked:
        return True
    if value != current:
        raise SettingsError(
            f"'{_ui_key(core_key)}' is set by an environment variable and cannot be "
            "changed here"
        )
    return False


def _apply_llm(
    table: Dict[str, Any],
    update: LLMUpdate,
    prefix: str,
    current: Optional[LLMSettings],
    locked: Set[str],
) -> bool:
    changed = False
    for name in update.model_fields_set:
        value = getattr(update, name)
        core_key = f"{prefix}.{name}"
        if name == "api_key":
            if not value or not value.strip():
                continue  # write-only: empty keeps the stored key
            _check_locked(locked, core_key, value, None)
            table["api_key"] = value.strip()
            changed = True
            continue
        if isinstance(value, str):
            value = value.strip()
        if value is None and name != "supports_images":  # None means "auto" there
            raise SettingsError(f"'{_ui_key(core_key)}' cannot be null")
        current_value = getattr(current, name) if current is not None else None
        if not _check_locked(locked, core_key, value, current_value):
            continue
        if name == "supports_images" and value is None:
            table.pop("supports_images", None)
        else:
            table[name] = value
        changed = True
    return changed


def _apply_section(
    raw: Dict[str, Any], section: str, update: BaseModel, current: Any, locked: Set[str]
) -> bool:
    changed = False
    for name in update.model_fields_set:
        value = getattr(update, name)
        if value is None:
            raise SettingsError(f"'{section}.{name}' cannot be null")
        if not _check_locked(
            locked, f"{section}.{name}", value, getattr(current, name)
        ):
            continue
        raw.setdefault(section, {})[name] = value
        changed = True
    return changed


def apply_update(update: SettingsUpdate) -> None:
    """Write a partial settings update and reload the configuration (sync).

    ``allow_registration`` is stored by the caller (database), not here.

    Raises:
        SettingsError: For env-locked keys or an invalid resulting configuration.
    """
    locked = config.env_overridden_keys()
    raw = copy.deepcopy(load_raw_config())
    llm_table = raw.setdefault("llm", {})
    changed = False

    if update.llm is not None:
        changed |= _apply_llm(
            llm_table, update.llm, "llm", config.llm.get("default"), locked
        )
    if "llm_vision" in update.model_fields_set:
        if update.llm_vision is None:
            if any(key.startswith("llm.vision.") for key in locked):
                raise SettingsError(
                    "The vision model is configured by environment variables"
                )
            changed |= llm_table.pop("vision", None) is not None
        else:
            vision_table = llm_table.setdefault("vision", {})
            if not isinstance(vision_table, dict):
                raise SettingsError("Invalid [llm.vision] section in config.toml")
            changed |= _apply_llm(
                vision_table,
                update.llm_vision,
                "llm.vision",
                config.llm.get("vision"),
                locked,
            )
    for section, current in (
        ("search", config.search_config or SearchSettings()),
        ("browser", config.browser_config),
        ("team", config.team),
        ("runtime", config.runtime),
    ):
        section_update = getattr(update, section)
        if section_update is not None:
            changed |= _apply_section(raw, section, section_update, current, locked)

    servers = None
    if update.mcp_servers is not None:
        servers = {
            name.strip(): server.model_dump(exclude_none=True)
            for name, server in update.mcp_servers.items()
        }
        if any(not name or len(name) > 64 for name in servers):
            raise SettingsError("MCP server names must be 1-64 characters")

    try:
        if changed:
            save_raw_config(raw)
        if servers is not None:
            save_mcp_servers_raw(servers)
    except (ValueError, ValidationError) as e:
        raise SettingsError(truncate(str(e), 1000)) from None
    if changed or servers is not None:
        config.reload()


# ------------------------------------------------------------------ test LLM


def _error_message(error: BaseException, api_key: str) -> str:
    text = f"{type(error).__name__}: {error}".strip()
    key = (api_key or "").strip()
    if len(key) >= 8:
        text = text.replace(key, "***")
    return truncate(text, 500)


async def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass


async def test_llm(which: str) -> Dict[str, Any]:
    """Send a tiny prompt with a fresh (uncached) client built from the current
    settings; returns ``{ok, message, latency_ms}``."""
    from app.llm import LLM
    from app.schema import Message

    settings = config.llm.get("default" if which == "llm" else "vision")
    if settings is None:
        return {
            "ok": False,
            "message": "The vision model is not configured",
            "latency_ms": 0,
        }
    if not settings.model.strip():
        return {"ok": False, "message": "No model is configured", "latency_ms": 0}

    started = time.perf_counter()
    llm = None
    try:
        llm = await asyncio.to_thread(LLM, llm_config=settings)
        async with asyncio.timeout(TEST_LLM_TIMEOUT):
            reply = await llm.ask([Message.user_message(TEST_LLM_PROMPT)], stream=False)
        ok, message = True, f"Model replied: {truncate(reply.strip(), 200)}"
    except TimeoutError:
        ok, message = False, f"No response within {int(TEST_LLM_TIMEOUT)} s"
    except Exception as e:
        ok, message = False, _error_message(e, settings.api_key)
    finally:
        if llm is not None:
            await _close_client(llm.client)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"ok": ok, "message": message, "latency_ms": latency_ms}
