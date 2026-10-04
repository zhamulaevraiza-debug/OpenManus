"""Application configuration.

Settings are read from ``config.toml`` in the configuration directory
(``OPENMANUS_CONFIG_DIR``, default ``<repo>/config``), falling back to
``config.example.toml``. Selected values can be overridden with environment variables
(see ``_LLM_ENV_FIELDS`` and :func:`_apply_env_overrides`); overridden keys are reported
by :meth:`Config.env_overridden_keys` so that UIs can show them as read-only.
"""

import copy
import json
import os
import sys
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.logger import logger


def get_project_root() -> Path:
    """Get the project root directory"""
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = get_project_root()
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE_NAME = "config.toml"
EXAMPLE_CONFIG_FILE_NAME = "config.example.toml"
MCP_CONFIG_FILE_NAME = "mcp.json"


def get_config_dir() -> Path:
    """Directory holding ``config.toml`` and ``mcp.json`` (``OPENMANUS_CONFIG_DIR``)."""
    value = os.environ.get("OPENMANUS_CONFIG_DIR", "").strip()
    return Path(value).expanduser().resolve() if value else DEFAULT_CONFIG_DIR


def _default_headless() -> bool:
    """Run browsers headless unless a display is available (desktop CLI usage)."""
    if sys.platform.startswith("linux"):
        return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return False


class LLMSettings(BaseModel):
    model: str = Field(..., description="Model name")
    base_url: str = Field(..., description="API base URL")
    api_key: str = Field(..., description="API key")
    max_tokens: int = Field(4096, description="Maximum number of tokens per request")
    max_input_tokens: Optional[int] = Field(
        None,
        description="Maximum input tokens to use across all requests (None for unlimited)",
    )
    temperature: float = Field(1.0, description="Sampling temperature")
    api_type: str = Field(..., description="Azure, Openai, or Ollama")
    api_version: str = Field(..., description="Azure Openai version if AzureOpenai")
    supports_images: Optional[bool] = Field(
        None,
        description="Whether the model accepts images (None: guess from the model name)",
    )


class ProxySettings(BaseModel):
    server: str = Field(None, description="Proxy server address")
    username: Optional[str] = Field(None, description="Proxy username")
    password: Optional[str] = Field(None, description="Proxy password")


class SearchSettings(BaseModel):
    engine: str = Field(default="Google", description="Search engine the llm to use")
    fallback_engines: List[str] = Field(
        default_factory=lambda: ["DuckDuckGo", "Baidu", "Bing"],
        description="Fallback search engines to try if the primary engine fails",
    )
    retry_delay: int = Field(
        default=60,
        description="Seconds to wait before retrying all engines again after they all fail",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of times to retry all engines when all fail",
    )
    lang: str = Field(
        default="en",
        description="Language code for search results (e.g., en, zh, fr)",
    )
    country: str = Field(
        default="us",
        description="Country code for search results (e.g., us, cn, uk)",
    )


class RunflowSettings(BaseModel):
    use_data_analysis_agent: bool = Field(
        default=False, description="Enable data analysis agent in run flow"
    )


class TeamSettings(BaseModel):
    """Settings of the multi-agent (team) planning flow."""

    max_plan_steps: int = Field(
        default=8, ge=1, le=50, description="Maximum number of steps in a team plan"
    )


class RuntimeSettings(BaseModel):
    """Limits applied to every agent run."""

    max_steps: int = Field(
        default=20, ge=1, le=500, description="Maximum steps of a single agent run"
    )


class BrowserSettings(BaseModel):
    headless: bool = Field(
        default_factory=_default_headless,
        description="Whether to run browser in headless mode (default: no display)",
    )
    disable_security: bool = Field(
        True, description="Disable browser security features"
    )
    extra_chromium_args: List[str] = Field(
        default_factory=list, description="Extra arguments to pass to the browser"
    )
    chrome_instance_path: Optional[str] = Field(
        None, description="Path to a Chrome instance to use"
    )
    wss_url: Optional[str] = Field(
        None, description="Connect to a browser instance via WebSocket"
    )
    cdp_url: Optional[str] = Field(
        None, description="Connect to a browser instance via CDP"
    )
    proxy: Optional[ProxySettings] = Field(
        None, description="Proxy settings for the browser"
    )
    max_content_length: int = Field(
        2000, description="Maximum length for content retrieval operations"
    )


class SandboxSettings(BaseModel):
    """Configuration for the execution sandbox"""

    use_sandbox: bool = Field(False, description="Whether to use the sandbox")
    image: str = Field("python:3.12-slim", description="Base image")
    work_dir: str = Field("/workspace", description="Container working directory")
    memory_limit: str = Field("512m", description="Memory limit")
    cpu_limit: float = Field(1.0, description="CPU limit")
    timeout: int = Field(300, description="Default command timeout (seconds)")
    network_enabled: bool = Field(
        False, description="Whether network access is allowed"
    )


class DaytonaSettings(BaseModel):
    daytona_api_key: Optional[str] = Field(
        None, description="Daytona API key (the sandbox agent is disabled without it)"
    )
    daytona_server_url: Optional[str] = Field(
        "https://app.daytona.io/api", description=""
    )
    daytona_target: Optional[str] = Field("us", description="enum ['eu', 'us']")
    sandbox_image_name: Optional[str] = Field("whitezxj/sandbox:0.1.0", description="")
    sandbox_entrypoint: Optional[str] = Field(
        "/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
        description="",
    )
    VNC_password: Optional[str] = Field(
        "123456", description="VNC password for the vnc service in sandbox"
    )


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server"""

    type: str = Field(..., description="Server connection type (sse or stdio)")
    url: Optional[str] = Field(None, description="Server URL for SSE connections")
    command: Optional[str] = Field(None, description="Command for stdio connections")
    args: List[str] = Field(
        default_factory=list, description="Arguments for stdio command"
    )


class MCPSettings(BaseModel):
    """Configuration for MCP (Model Context Protocol)"""

    server_reference: str = Field(
        "app.mcp.server", description="Module reference for the MCP server"
    )
    servers: Dict[str, MCPServerConfig] = Field(
        default_factory=dict, description="MCP server configurations"
    )

    @classmethod
    def load_server_config(cls) -> Dict[str, MCPServerConfig]:
        """Load MCP server configuration from ``mcp.json``; invalid entries are skipped."""
        servers = {}
        for server_id, server_config in load_mcp_servers_raw().items():
            try:
                servers[server_id] = MCPServerConfig(**server_config)
            except (TypeError, ValidationError) as e:
                logger.error(f"Ignoring invalid MCP server '{server_id}': {e}")
        return servers


class AppConfig(BaseModel):
    llm: Dict[str, LLMSettings]
    sandbox: Optional[SandboxSettings] = Field(
        None, description="Sandbox configuration"
    )
    browser_config: Optional[BrowserSettings] = Field(
        None, description="Browser configuration"
    )
    search_config: Optional[SearchSettings] = Field(
        None, description="Search configuration"
    )
    mcp_config: Optional[MCPSettings] = Field(None, description="MCP configuration")
    run_flow_config: Optional[RunflowSettings] = Field(
        None, description="Run flow configuration"
    )
    daytona_config: Optional[DaytonaSettings] = Field(
        None, description="Daytona configuration"
    )
    team_config: TeamSettings = Field(
        default_factory=TeamSettings, description="Team flow configuration"
    )
    runtime_config: RuntimeSettings = Field(
        default_factory=RuntimeSettings, description="Agent runtime limits"
    )
    workspace_root: Path = Field(WORKSPACE_ROOT, description="Root of the workspace")

    model_config = ConfigDict(arbitrary_types_allowed=True)


# --------------------------------------------------------------------------------------
# Environment overrides
# --------------------------------------------------------------------------------------

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ValueError(f"expected true/false, got '{value}'")


def _parse_optional_bool(value: str) -> Optional[bool]:
    """Boolean that also accepts ``auto`` (None)."""
    return None if value.strip().lower() == "auto" else _parse_bool(value)


# Environment variable suffix -> (LLM setting name, parser). Applied with the prefixes
# OPENMANUS_LLM_ (the [llm] table) and OPENMANUS_LLM_VISION_ (the [llm.vision] table).
_LLM_ENV_FIELDS: Dict[str, Tuple[str, Callable[[str], Any]]] = {
    "MODEL": ("model", str),
    "BASE_URL": ("base_url", str),
    "API_KEY": ("api_key", str),
    "API_TYPE": ("api_type", str),
    "API_VERSION": ("api_version", str),
    "MAX_TOKENS": ("max_tokens", int),
    "TEMPERATURE": ("temperature", float),
    "SUPPORTS_IMAGES": ("supports_images", _parse_optional_bool),
}


def _env_value(name: str) -> Optional[str]:
    """Value of an environment variable; unset and empty values count as missing."""
    value = os.environ.get(name)
    return value if value is not None and value.strip() != "" else None


def _apply_env_overrides(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], Set[str]]:
    """Return a copy of ``raw`` with environment overrides applied.

    Returns the merged dict and the set of overridden keys as dotted TOML paths
    (``llm.api_key``, ``llm.vision.model``, ``browser.headless``) plus
    ``workspace_root`` for ``OPENMANUS_WORKSPACE_ROOT``.
    """
    merged = copy.deepcopy(raw)
    overridden: Set[str] = set()

    for prefix, path in (
        ("OPENMANUS_LLM_VISION_", ("vision",)),
        ("OPENMANUS_LLM_", ()),
    ):
        for suffix, (field, parse) in _LLM_ENV_FIELDS.items():
            env_name = prefix + suffix
            value = _env_value(env_name)
            if value is None:
                continue
            try:
                parsed = parse(value.strip())
            except ValueError as e:
                logger.warning(f"Ignoring invalid {env_name}: {e}")
                continue
            table = merged.setdefault("llm", {})
            for key in path:
                table = table.setdefault(key, {})
            table[field] = parsed
            overridden.add(".".join(("llm", *path, field)))

    headless = _env_value("OPENMANUS_BROWSER_HEADLESS")
    if headless is not None:
        try:
            merged.setdefault("browser", {})["headless"] = _parse_bool(headless)
            overridden.add("browser.headless")
        except ValueError as e:
            logger.warning(f"Ignoring invalid OPENMANUS_BROWSER_HEADLESS: {e}")

    workspace = _env_value("OPENMANUS_WORKSPACE_ROOT")
    if workspace is not None:
        merged["workspace_root"] = workspace
        overridden.add("workspace_root")

    return merged, overridden


# --------------------------------------------------------------------------------------
# Building AppConfig from raw TOML data
# --------------------------------------------------------------------------------------

_LLM_DEFAULTS: Dict[str, Any] = {
    "model": "",
    "base_url": "",
    "api_key": "",
    "max_tokens": 4096,
    "max_input_tokens": None,
    "temperature": 1.0,
    "api_type": "",
    "api_version": "",
    "supports_images": None,
}


def _build_llm_settings(raw_llm: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    base = {k: v for k, v in raw_llm.items() if not isinstance(v, dict)}
    default_settings = {**_LLM_DEFAULTS, **base}
    overrides = {k: v for k, v in raw_llm.items() if isinstance(v, dict)}
    return {
        "default": default_settings,
        **{
            name: {**default_settings, **override}
            for name, override in overrides.items()
        },
    }


def _build_browser_settings(raw_browser: Dict[str, Any]) -> BrowserSettings:
    params = {
        k: v
        for k, v in raw_browser.items()
        if k in BrowserSettings.model_fields and k != "proxy" and v is not None
    }
    proxy_config = raw_browser.get("proxy") or {}
    if proxy_config.get("server"):
        params["proxy"] = ProxySettings(
            **{
                k: v
                for k, v in proxy_config.items()
                if k in ("server", "username", "password") and v
            }
        )
    return BrowserSettings(**params)


def _build_app_config(raw: Dict[str, Any]) -> AppConfig:
    """Validate raw configuration data (env overrides already applied)."""
    mcp_raw = raw.get("mcp") or {}
    workspace_root = raw.get("workspace_root")
    return AppConfig(
        llm=_build_llm_settings(raw.get("llm", {})),
        sandbox=SandboxSettings(**(raw.get("sandbox") or {})),
        browser_config=_build_browser_settings(raw.get("browser") or {}),
        search_config=SearchSettings(**(raw.get("search") or {})),
        mcp_config=MCPSettings(
            **{k: v for k, v in mcp_raw.items() if k != "servers"},
            servers=MCPSettings.load_server_config(),
        ),
        run_flow_config=RunflowSettings(**(raw.get("runflow") or {})),
        daytona_config=DaytonaSettings(**(raw.get("daytona") or {})),
        team_config=TeamSettings(**(raw.get("team") or {})),
        runtime_config=RuntimeSettings(**(raw.get("runtime") or {})),
        workspace_root=(
            Path(workspace_root).expanduser().resolve()
            if workspace_root
            else WORKSPACE_ROOT
        ),
    )


# --------------------------------------------------------------------------------------
# Raw config files (used by the admin settings UI)
# --------------------------------------------------------------------------------------


def _config_file_path() -> Path:
    """Path of the configuration file that is (or would be) loaded."""
    config_dir = get_config_dir()
    config_path = config_dir / CONFIG_FILE_NAME
    if config_path.exists():
        return config_path
    for example_path in (
        config_dir / EXAMPLE_CONFIG_FILE_NAME,
        DEFAULT_CONFIG_DIR / EXAMPLE_CONFIG_FILE_NAME,
    ):
        if example_path.exists():
            return example_path
    raise FileNotFoundError("No configuration file found in config directory")


def load_raw_config() -> Dict[str, Any]:
    """Raw contents of the active configuration file, without env overrides."""
    with _config_file_path().open("rb") as f:
        return tomllib.load(f)


def _drop_none(value: Any) -> Any:
    """Remove ``None`` values recursively (TOML has no null)."""
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value if v is not None]
    return value


def _atomic_write(path: Path, content: bytes) -> None:
    """Write ``content`` atomically with mode 600, keeping one ``.bak`` copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            backup = path.with_name(path.name + ".bak")
            backup.write_bytes(path.read_bytes())
            os.chmod(backup, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def save_raw_config(data: Dict[str, Any]) -> None:
    """Validate and atomically write ``config.toml`` in the config directory.

    Raises:
        ValueError: when ``data`` does not form a valid configuration.
    """
    clean = _drop_none(data)
    try:
        _build_app_config(_apply_env_overrides(clean)[0])
    except ValidationError as e:
        raise ValueError(f"Invalid configuration: {e}") from e
    _atomic_write(get_config_dir() / CONFIG_FILE_NAME, tomli_w.dumps(clean).encode())


def load_mcp_servers_raw() -> Dict[str, Dict[str, Any]]:
    """The ``mcpServers`` mapping from ``mcp.json`` ({} when missing or unreadable)."""
    path = get_config_dir() / MCP_CONFIG_FILE_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to read MCP server config {path}: {e}")
        return {}
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    return servers if isinstance(servers, dict) else {}


def save_mcp_servers_raw(servers: Dict[str, Dict[str, Any]]) -> None:
    """Validate and atomically write the ``mcpServers`` mapping to ``mcp.json``.

    Raises:
        ValueError: when a server entry is invalid.
    """
    for server_id, server in servers.items():
        try:
            parsed = MCPServerConfig(**server)
        except (TypeError, ValidationError) as e:
            raise ValueError(f"Invalid MCP server '{server_id}': {e}") from e
        if parsed.type not in ("sse", "stdio"):
            raise ValueError(f"MCP server '{server_id}': type must be 'sse' or 'stdio'")
        if parsed.type == "sse" and not parsed.url:
            raise ValueError(f"MCP server '{server_id}': url is required for sse")
        if parsed.type == "stdio" and not parsed.command:
            raise ValueError(f"MCP server '{server_id}': command is required for stdio")
    content = json.dumps(
        {"mcpServers": _drop_none(servers)}, indent=2, ensure_ascii=False
    )
    _atomic_write(get_config_dir() / MCP_CONFIG_FILE_NAME, (content + "\n").encode())


_PLACEHOLDER_API_KEYS = {"", "your_api_key", "your-api-key", "azure api key", "sk-..."}


def _is_placeholder_api_key(api_key: str) -> bool:
    lowered = api_key.strip().lower()
    return lowered in _PLACEHOLDER_API_KEYS or ("your" in lowered and "key" in lowered)


# --------------------------------------------------------------------------------------
# Config singleton
# --------------------------------------------------------------------------------------


class Config:
    _instance = None
    _lock = threading.RLock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._reload_hooks: List[Callable[[], None]] = []
                    self._load_initial_config()
                    self._initialized = True

    def _load_initial_config(self) -> None:
        config_path = _config_file_path()
        if config_path.name != CONFIG_FILE_NAME:
            logger.warning(
                f"{get_config_dir() / CONFIG_FILE_NAME} not found, "
                f"using the example configuration {config_path}"
            )
        with config_path.open("rb") as f:
            raw = tomllib.load(f)
        merged, overridden = _apply_env_overrides(raw)
        self._config = _build_app_config(merged)
        self._config_path = config_path
        self._env_overridden = overridden

    def reload(self) -> None:
        """Re-read the configuration files and environment, then run reload hooks.

        The current configuration is kept when the new one is invalid (the error is
        raised). Hooks (e.g. ``LLM.reset_instances``) run after a successful reload.
        """
        with self._lock:
            self._load_initial_config()
            hooks = list(self._reload_hooks)
        for hook in hooks:
            try:
                hook()
            except Exception as e:
                logger.error(f"Config reload hook {hook!r} failed: {e}")

    def add_reload_hook(self, hook: Callable[[], None]) -> None:
        """Register a callable invoked after every successful :meth:`reload`."""
        with self._lock:
            if hook not in self._reload_hooks:
                self._reload_hooks.append(hook)

    def env_overridden_keys(self) -> Set[str]:
        """Dotted keys whose values come from environment variables."""
        return set(self._env_overridden)

    def llm_configured(self) -> bool:
        """Whether the default LLM has a model and a usable credential."""
        settings = self.llm.get("default")
        if settings is None or not settings.model.strip():
            return False
        if settings.api_type.strip().lower() in ("ollama", "aws"):
            return True
        return not _is_placeholder_api_key(settings.api_key)

    @staticmethod
    def load_raw_config() -> Dict[str, Any]:
        return load_raw_config()

    @staticmethod
    def save_raw_config(data: Dict[str, Any]) -> None:
        save_raw_config(data)

    @staticmethod
    def load_mcp_servers_raw() -> Dict[str, Dict[str, Any]]:
        return load_mcp_servers_raw()

    @staticmethod
    def save_mcp_servers_raw(servers: Dict[str, Dict[str, Any]]) -> None:
        save_mcp_servers_raw(servers)

    @property
    def config_dir(self) -> Path:
        """Directory of the configuration files (``OPENMANUS_CONFIG_DIR``)."""
        return get_config_dir()

    @property
    def config_path(self) -> Path:
        """The configuration file the current settings were loaded from."""
        return self._config_path

    @property
    def llm(self) -> Dict[str, LLMSettings]:
        return self._config.llm

    @property
    def sandbox(self) -> SandboxSettings:
        return self._config.sandbox

    @property
    def daytona(self) -> DaytonaSettings:
        return self._config.daytona_config

    @property
    def browser_config(self) -> Optional[BrowserSettings]:
        return self._config.browser_config

    @property
    def search_config(self) -> Optional[SearchSettings]:
        return self._config.search_config

    @property
    def mcp_config(self) -> MCPSettings:
        """Get the MCP configuration"""
        return self._config.mcp_config

    @property
    def run_flow_config(self) -> RunflowSettings:
        """Get the Run Flow configuration"""
        return self._config.run_flow_config

    @property
    def team(self) -> TeamSettings:
        """Team (multi-agent) flow settings"""
        return self._config.team_config

    @property
    def runtime(self) -> RuntimeSettings:
        """Agent runtime limits"""
        return self._config.runtime_config

    @property
    def workspace_root(self) -> Path:
        """Get the workspace root directory"""
        return self._config.workspace_root

    @property
    def root_path(self) -> Path:
        """Get the root path of the application"""
        return PROJECT_ROOT


config = Config()
