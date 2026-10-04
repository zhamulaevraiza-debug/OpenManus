"""Web server settings read from ``OPENMANUS_*`` environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field, replace
from importlib import metadata
from pathlib import Path
from typing import Mapping, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VERSION = "0.1.0"

COOKIE_SECURE_MODES = ("auto", "true", "false")


def app_version() -> str:
    """Version of the installed ``openmanus`` distribution (or the source default)."""
    try:
        return metadata.version("openmanus")
    except metadata.PackageNotFoundError:
        return DEFAULT_VERSION


def _parse_bool(name: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}: expected true/false, got '{value}'")


def _parse_positive(name: str, value: str, parse=int):
    try:
        number = parse(value.strip())
    except ValueError:
        raise ValueError(f"{name}: expected a number, got '{value}'") from None
    if number <= 0:
        raise ValueError(f"{name}: must be greater than zero")
    return number


@dataclass(frozen=True)
class WebSettings:
    """Immutable settings of one web server instance.

    Use :meth:`from_env` in production; tests build instances directly or override
    single values with :meth:`with_overrides`.
    """

    data_dir: Path
    database_url: str
    static_dir: Path
    secret_key: Optional[str] = field(default=None, repr=False)
    admin_username: str = "admin"
    admin_password: Optional[str] = field(default=None, repr=False)
    allow_registration: bool = False
    cookie_secure: str = "auto"
    session_days: int = 14
    max_concurrent_runs: int = 4
    max_runs_per_user: int = 2
    max_queued_runs: int = 16
    run_timeout: float = 3600.0
    human_input_timeout: float = 1800.0
    max_upload_mb: int = 50
    cors_origins: Tuple[str, ...] = ()
    trust_proxy: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "WebSettings":
        """Build settings from ``OPENMANUS_*`` variables (``os.environ`` by default).

        Raises:
            ValueError: When a variable has an invalid value.
        """
        env = os.environ if environ is None else environ

        def get(name: str) -> Optional[str]:
            value = env.get(f"OPENMANUS_{name}")
            return value.strip() if value is not None and value.strip() else None

        data_dir = Path(get("DATA_DIR") or PROJECT_ROOT / "data").expanduser()
        data_dir = data_dir.resolve()
        values = {}
        for name, attr, parse in (
            ("SESSION_DAYS", "session_days", int),
            ("MAX_CONCURRENT_RUNS", "max_concurrent_runs", int),
            ("MAX_RUNS_PER_USER", "max_runs_per_user", int),
            ("MAX_QUEUED_RUNS", "max_queued_runs", int),
            ("RUN_TIMEOUT", "run_timeout", float),
            ("HUMAN_INPUT_TIMEOUT", "human_input_timeout", float),
            ("MAX_UPLOAD_MB", "max_upload_mb", int),
            ("PORT", "port", int),
        ):
            raw = get(name)
            if raw is not None:
                values[attr] = _parse_positive(f"OPENMANUS_{name}", raw, parse)
        for name, attr in (
            ("ALLOW_REGISTRATION", "allow_registration"),
            ("TRUST_PROXY", "trust_proxy"),
        ):
            raw = get(name)
            if raw is not None:
                values[attr] = _parse_bool(f"OPENMANUS_{name}", raw)

        cookie_secure = (get("COOKIE_SECURE") or "auto").lower()
        if cookie_secure in ("1", "yes", "on"):
            cookie_secure = "true"
        elif cookie_secure in ("0", "no", "off"):
            cookie_secure = "false"
        if cookie_secure not in COOKIE_SECURE_MODES:
            raise ValueError("OPENMANUS_COOKIE_SECURE: expected auto, true or false")

        origins = tuple(
            origin.strip().rstrip("/")
            for origin in (get("CORS_ORIGINS") or "").split(",")
            if origin.strip()
        )
        static_dir = Path(get("STATIC_DIR") or PROJECT_ROOT / "web" / "dist")

        return cls(
            data_dir=data_dir,
            database_url=get("DATABASE_URL")
            or f"sqlite+aiosqlite:///{data_dir / 'openmanus.db'}",
            static_dir=static_dir.expanduser().resolve(),
            secret_key=get("SECRET_KEY"),
            admin_username=(get("ADMIN_USERNAME") or "admin").lower(),
            admin_password=env.get("OPENMANUS_ADMIN_PASSWORD") or None,
            cookie_secure=cookie_secure,
            cors_origins=origins,
            host=get("HOST") or "0.0.0.0",
            **values,
        )

    def with_overrides(self, **changes) -> "WebSettings":
        """A copy with some values replaced."""
        return replace(self, **changes)

    # ------------------------------------------------------------------ paths

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def uploads_tmp_dir(self) -> Path:
        """Staging area for uploads (same file system as the workspaces)."""
        return self.data_dir / "tmp" / "uploads"

    @property
    def secret_key_file(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def initial_password_file(self) -> Path:
        return self.data_dir / "initial_admin_password.txt"

    @property
    def max_upload_bytes(self) -> int:
        """Maximum size of a single uploaded file."""
        return self.max_upload_mb * 1024 * 1024

    def user_workspaces(self, user_id: str) -> Path:
        return self.workspaces_dir / user_id

    def workspace_path(self, user_id: str, conversation_id: str) -> Path:
        """Workspace directory of a conversation (may not exist yet)."""
        return self.workspaces_dir / user_id / conversation_id

    def run_artifacts(self, run_id: str) -> Path:
        return self.artifacts_dir / run_id

    # ------------------------------------------------------------ preparation

    def ensure_dirs(self) -> None:
        """Create the data directory layout with restrictive permissions.

        Workspace directories are traversable (0711) so that a dedicated exec user can
        reach its own workspace; everything else is private to the server user.
        """
        for path, mode in (
            (self.data_dir, 0o711),
            (self.workspaces_dir, 0o711),
            (self.artifacts_dir, 0o700),
            (self.uploads_tmp_dir.parent, 0o700),
            (self.uploads_tmp_dir, 0o700),
        ):
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                os.chmod(path, mode)

    def load_secret_key(self) -> str:
        """The JWT signing key: from the environment, else ``DATA_DIR/secret.key``
        (generated once with mode 600)."""
        if self.secret_key:
            return self.secret_key
        path = self.secret_key_file
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
            raise RuntimeError(
                f"Secret key file {path} is empty; delete it to regenerate"
            ) from None
        key = secrets.token_urlsafe(48)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        return key
