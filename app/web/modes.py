"""Run modes offered by the web API: auto, chat, team and every registered agent."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.agent.registry import AgentSpec, available_agents, list_agents
from app.logger import logger


AUTO = "auto"
CHAT = "chat"
TEAM = "team"
BUILTIN_MODES = (AUTO, CHAT, TEAM)


def _availability(spec: AgentSpec) -> Tuple[bool, Optional[str]]:
    try:
        return spec.is_available()
    except Exception as e:  # a broken check must not break the API
        logger.error(f"Availability check of agent '{spec.key}' failed: {e}")
        return False, "Availability check failed"


def agent_infos() -> List[Dict]:
    """AgentInfo dicts of all registered agents."""
    infos = []
    for spec in list_agents():
        available, reason = _availability(spec)
        infos.append(
            {
                "key": spec.key,
                "name": spec.name,
                "description": spec.description,
                "icon": spec.icon,
                "available": bool(available),
                "reason": None if available else reason,
                "team_member": spec.team_member,
            }
        )
    return infos


def validate_mode(mode: str) -> str:
    """Check that a run can use ``mode`` now.

    Raises:
        ValueError: With a user-facing message for unknown or unavailable modes.
    """
    mode = mode.strip()
    if mode in (AUTO, CHAT):
        return mode
    if mode == TEAM:
        if not available_agents(team_only=True):
            raise ValueError("No team agents are available")
        return mode
    for spec in list_agents():
        if spec.key == mode:
            available, reason = _availability(spec)
            if not available:
                raise ValueError(f"Agent '{spec.name}' is not available: {reason}")
            return mode
    raise ValueError(f"Unknown mode '{mode}'")


def is_known_mode(mode: str) -> bool:
    """Whether ``mode`` names a builtin mode or a registered agent."""
    return mode in BUILTIN_MODES or any(spec.key == mode for spec in list_agents())
