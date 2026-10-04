"""Registry of the agents offered by OpenManus (web UI, team flow and CLI).

Agent classes are imported lazily by the factories, so listing agents is cheap and
optional dependencies (e.g. the Daytona SDK) are only needed when actually used.
"""

import asyncio
import importlib.util
import shutil
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from app.agent.base import BaseAgent
from app.config import config
from app.logger import logger


Availability = Tuple[bool, Optional[str]]
AgentFactory = Callable[[], Awaitable[BaseAgent]]

DEFAULT_AGENT_KEY = "manus"
CLEANUP_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class AgentSpec:
    """Description of an agent and how to create it.

    Attributes:
        key: Stable identifier (also used as the agent name in events).
        name: Display name.
        description: What the agent is good at (shown to users, router and planner).
        icon: lucide icon name.
        factory: Creates a FRESH, initialised agent whose prompts use the current
            workspace; call it inside the task that will run and clean up the agent.
        is_available: Returns ``(available, reason)``; ``reason`` is a user-facing
            English explanation when unavailable.
        team_member: Whether the team flow may assign plan steps to the agent.
    """

    key: str
    name: str
    description: str
    icon: str
    factory: AgentFactory
    is_available: Callable[[], Availability]
    team_member: bool = True


def _always_available() -> Availability:
    return True, None


def _shell_available() -> Availability:
    if shutil.which("bash") is None:
        return False, "bash is not available on this server"
    return True, None


def _daytona_available() -> Availability:
    if not (config.daytona and config.daytona.daytona_api_key):
        return False, "Daytona API key not configured"
    if importlib.util.find_spec("daytona") is None:
        return False, "Daytona SDK is not installed"
    return True, None


def _agent_kwargs(key: str, name: str) -> dict:
    return {"name": key, "title": name, "max_steps": config.runtime.max_steps}


async def _create_manus(kwargs: dict) -> BaseAgent:
    from app.agent.manus import Manus

    return await Manus.create(**kwargs)


async def _create_browser(kwargs: dict) -> BaseAgent:
    from app.agent.browser import BrowserAgent

    return BrowserAgent(**kwargs)


async def _create_researcher(kwargs: dict) -> BaseAgent:
    from app.agent.researcher import ResearcherAgent

    return ResearcherAgent(**kwargs)


async def _create_coder(kwargs: dict) -> BaseAgent:
    from app.agent.swe import SWEAgent

    return SWEAgent(**kwargs)


async def _create_data_analyst(kwargs: dict) -> BaseAgent:
    from app.agent.data_analysis import DataAnalysis

    return DataAnalysis(**kwargs)


async def _create_writer(kwargs: dict) -> BaseAgent:
    from app.agent.writer import WriterAgent

    return WriterAgent(**kwargs)


async def _create_sandbox(kwargs: dict) -> BaseAgent:
    from app.agent.sandbox_agent import SandboxManus

    return await SandboxManus.create(**kwargs)


def _spec(
    key: str,
    name: str,
    description: str,
    icon: str,
    create: Callable[[dict], Awaitable[BaseAgent]],
    is_available: Callable[[], Availability] = _always_available,
    team_member: bool = True,
) -> AgentSpec:
    async def factory() -> BaseAgent:
        return await create(_agent_kwargs(key, name))

    return AgentSpec(key, name, description, icon, factory, is_available, team_member)


_SPECS: Dict[str, AgentSpec] = {
    spec.key: spec
    for spec in (
        _spec(
            "manus",
            "Manus",
            "General-purpose agent: searches and browses the web, runs Python, "
            "creates and edits files, and uses configured MCP tools",
            "bot",
            _create_manus,
        ),
        _spec(
            "browser",
            "Browser",
            "Operates a web browser interactively: navigates sites, clicks, fills in "
            "forms, extracts page content and takes screenshots",
            "globe",
            _create_browser,
        ),
        _spec(
            "researcher",
            "Researcher",
            "Researches topics across multiple web sources, cross-checks facts and "
            "reports findings with cited URLs, optionally as a saved report",
            "search",
            _create_researcher,
        ),
        _spec(
            "coder",
            "Coder",
            "Software engineer: writes, runs and tests code and scripts in the "
            "workspace using the shell, a file editor and Python",
            "code",
            _create_coder,
            is_available=_shell_available,
        ),
        _spec(
            "data_analyst",
            "Data Analyst",
            "Analyzes data files with Python (pandas, matplotlib), builds charts and "
            "writes analysis reports",
            "bar-chart-3",
            _create_data_analyst,
        ),
        _spec(
            "writer",
            "Writer",
            "Writes polished long-form documents (reports, articles, proposals, "
            "documentation) and saves them as Markdown, Word (DOCX) or HTML",
            "pen-line",
            _create_writer,
        ),
        _spec(
            "sandbox",
            "Sandbox",
            "General-purpose agent working in an isolated Daytona cloud sandbox with "
            "its own browser, shell and file system",
            "box",
            _create_sandbox,
            is_available=_daytona_available,
            team_member=False,
        ),
    )
}


def list_agents() -> List[AgentSpec]:
    """All registered agents, in display order."""
    return list(_SPECS.values())


def available_agents(team_only: bool = False) -> List[AgentSpec]:
    """Agents that can currently be used (optionally only team members)."""
    return [
        spec
        for spec in _SPECS.values()
        if spec.is_available()[0] and (spec.team_member or not team_only)
    ]


def get_agent_spec(key: str) -> AgentSpec:
    """The agent registered under ``key``.

    Raises:
        ValueError: If no agent has this key.
    """
    spec = _SPECS.get(key)
    if spec is None:
        raise ValueError(f"Unknown agent '{key}'")
    return spec


async def create_agent(key: str) -> BaseAgent:
    """Create a fresh agent; it must be cleaned up (``dispose_agent``) in the same task.

    Raises:
        ValueError: If the agent is unknown or currently unavailable.
    """
    spec = get_agent_spec(key)
    available, reason = spec.is_available()
    if not available:
        raise ValueError(f"Agent '{spec.name}' is not available: {reason}")
    return await spec.factory()


async def dispose_agent(agent: BaseAgent) -> None:
    """Release an agent's resources (browser, processes, MCP sessions, sandbox).

    Must be awaited in the task that created the agent. Errors are logged, a hanging
    cleanup is abandoned after ``CLEANUP_TIMEOUT_SECONDS``; cancellation propagates.
    """
    cleanup = getattr(agent, "cleanup", None)
    if cleanup is None:
        return
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
            await cleanup()
    except TimeoutError:
        logger.error(f"Cleanup of agent '{agent.name}' timed out")
    except Exception as e:
        logger.error(f"Cleanup of agent '{agent.name}' failed: {e}")
