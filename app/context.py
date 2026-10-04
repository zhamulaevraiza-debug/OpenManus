"""Per-run execution context shared by agents, tools and the web layer.

A ``RunContext`` is bound to the current asyncio task via a ``ContextVar``. The web
server sets one for every agent run so that deeply nested code (tools, flows, the LLM
client) can find the run's workspace, report progress events, ask the user a
question and account token usage — without threading extra arguments everywhere.

When no context is active (CLI usage) every helper falls back to the classic
single-user behaviour: the global workspace, no event sink and ``input()`` for
questions.
"""

from __future__ import annotations

import os
import stat
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from app.exceptions import WorkspaceViolation


EventSink = Callable[[str, dict], None]
HumanInputProvider = Callable[[str], Awaitable[str]]


@dataclass
class RunContext:
    """State of one agent run (one user request)."""

    run_id: str
    workspace: Path
    emit_sink: Optional[EventSink] = None
    ask_human: Optional[HumanInputProvider] = None
    restrict_to_workspace: bool = True
    usage: Dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "completion_tokens": 0}
    )
    extra: Dict[str, Any] = field(default_factory=dict)


_current: ContextVar[Optional[RunContext]] = ContextVar("openmanus_run", default=None)


def current_run() -> Optional[RunContext]:
    """Return the active run context, if any."""
    return _current.get()


def set_run(ctx: RunContext) -> Token:
    """Bind ``ctx`` to the current task. Returns a token for :func:`reset_run`."""
    return _current.set(ctx)


def reset_run(token: Token) -> None:
    """Restore the context that was active before :func:`set_run`."""
    _current.reset(token)


def get_workspace() -> Path:
    """Workspace directory for the current run (or the global CLI workspace)."""
    ctx = _current.get()
    if ctx is not None:
        return ctx.workspace
    from app.config import config

    return Path(config.workspace_root)


def emit(event_type: str, **data: Any) -> None:
    """Report a progress event to the active run. Never raises."""
    ctx = _current.get()
    if ctx is None or ctx.emit_sink is None:
        return
    try:
        ctx.emit_sink(event_type, data)
    except Exception as e:  # pragma: no cover - defensive
        try:
            from app.logger import logger

            logger.warning(f"Event sink failed for {event_type}: {e}")
        except Exception:
            pass


def add_usage(input_tokens: int, completion_tokens: int) -> None:
    """Accumulate LLM token usage for the active run."""
    ctx = _current.get()
    if ctx is None:
        return
    ctx.usage["input_tokens"] = ctx.usage.get("input_tokens", 0) + int(
        input_tokens or 0
    )
    ctx.usage["completion_tokens"] = ctx.usage.get("completion_tokens", 0) + int(
        completion_tokens or 0
    )


def resolve_in_workspace(path: Union[str, Path]) -> Path:
    """Resolve a user/LLM supplied path against the current workspace.

    Relative paths are resolved inside the workspace. When the active run restricts
    file access, the final path (after following symlinks) must stay inside the
    workspace, otherwise :class:`WorkspaceViolation` is raised.
    """
    workspace = get_workspace()
    raw = (
        Path(os.path.expanduser(str(path))) if str(path).startswith("~") else Path(path)
    )
    candidate = raw if raw.is_absolute() else workspace / raw
    resolved = candidate.resolve()

    ctx = _current.get()
    if ctx is not None and ctx.restrict_to_workspace:
        root = workspace.resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise WorkspaceViolation(
                f"Access denied: '{path}' is outside the workspace {root}"
            )
    return resolved


def _exec_user_ids() -> Optional[tuple[int, int]]:
    """(uid, gid) of OPENMANUS_EXEC_USER when privilege dropping is possible."""
    name = os.environ.get("OPENMANUS_EXEC_USER", "").strip()
    if not name or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    try:
        import pwd

        entry = pwd.getpwnam(name)
    except (KeyError, ImportError):
        return None
    return entry.pw_uid, entry.pw_gid


def prepare_workspace(path: Union[str, Path]) -> Path:
    """Create a workspace directory and hand it to the exec user if configured."""
    workspace = Path(path)
    workspace.mkdir(parents=True, exist_ok=True)
    ids = _exec_user_ids()
    if ids is not None:
        uid, gid = ids
        for root, dirs, files in os.walk(workspace):
            for name in [*dirs, *files]:
                entry = os.path.join(root, name)
                try:
                    st = os.lstat(entry)
                    # A hard link may point at a protected file elsewhere (e.g. the
                    # secret key); chowning it would hand that file to agent code.
                    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                        continue
                    os.chown(entry, uid, gid, follow_symlinks=False)
                except OSError:
                    pass
        try:
            os.chown(workspace, uid, gid)
        except OSError:
            pass
    return workspace.resolve()
