"""OpenManus tools.

Exports are resolved lazily (PEP 562) so that ``from app.tool import Terminate`` does
not import the heavy browser/search/crawler stacks.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any


_EXPORTS = {
    "BaseTool": "app.tool.base",
    "Bash": "app.tool.bash",
    "BrowserUseTool": "app.tool.browser_use_tool",
    "Crawl4aiTool": "app.tool.crawl4ai",
    "CreateChatCompletion": "app.tool.create_chat_completion",
    "PlanningTool": "app.tool.planning",
    "StrReplaceEditor": "app.tool.str_replace_editor",
    "Terminate": "app.tool.terminate",
    "ToolCollection": "app.tool.tool_collection",
    "WebSearch": "app.tool.web_search",
}

__all__ = list(_EXPORTS)

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from app.tool.base import BaseTool
    from app.tool.bash import Bash
    from app.tool.browser_use_tool import BrowserUseTool
    from app.tool.crawl4ai import Crawl4aiTool
    from app.tool.create_chat_completion import CreateChatCompletion
    from app.tool.planning import PlanningTool
    from app.tool.str_replace_editor import StrReplaceEditor
    from app.tool.terminate import Terminate
    from app.tool.tool_collection import ToolCollection
    from app.tool.web_search import WebSearch


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
