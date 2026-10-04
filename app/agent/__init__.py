"""OpenManus agents.

Exports are resolved lazily (PEP 562) so that importing a single agent module, or the
registry, does not import every agent's tool stack.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any


_EXPORTS = {
    "BaseAgent": "app.agent.base",
    "BrowserAgent": "app.agent.browser",
    "DataAnalysis": "app.agent.data_analysis",
    "Manus": "app.agent.manus",
    "MCPAgent": "app.agent.mcp",
    "ReActAgent": "app.agent.react",
    "ResearcherAgent": "app.agent.researcher",
    "SWEAgent": "app.agent.swe",
    "ToolCallAgent": "app.agent.toolcall",
    "WriterAgent": "app.agent.writer",
}

__all__ = list(_EXPORTS)

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from app.agent.base import BaseAgent
    from app.agent.browser import BrowserAgent
    from app.agent.data_analysis import DataAnalysis
    from app.agent.manus import Manus
    from app.agent.mcp import MCPAgent
    from app.agent.react import ReActAgent
    from app.agent.researcher import ResearcherAgent
    from app.agent.swe import SWEAgent
    from app.agent.toolcall import ToolCallAgent
    from app.agent.writer import WriterAgent


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
