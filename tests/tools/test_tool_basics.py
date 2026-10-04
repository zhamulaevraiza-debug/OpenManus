"""ToolResult rendering, ToolCollection input handling and lazy package exports."""

import importlib
import pkgutil
import subprocess
import sys

import pytest

import app.tool
from app.tool.base import BaseTool, CLIResult, ToolResult
from app.tool.tool_collection import ToolCollection


@pytest.mark.parametrize(
    "result, expected",
    [
        (ToolResult(output="text"), "text"),
        (ToolResult(), ""),
        (ToolResult(output={"a": 1}), '{"a": 1}'),
        (ToolResult(output=[1, "x"]), '[1, "x"]'),
        (ToolResult(output=3), "3"),
        (CLIResult(system="tool has been restarted."), "tool has been restarted."),
        (ToolResult(output="out", system="note"), "out\nnote"),
        (ToolResult(output="ignored", error="bad"), "Error: bad"),
        (ToolResult(base64_image="aGk="), ""),
    ],
)
def test_tool_result_str(result, expected):
    assert str(result) == expected


def test_tool_result_replace_keeps_type():
    replaced = CLIResult(output="a").replace(error="e")
    assert isinstance(replaced, CLIResult)
    assert replaced.output == "a" and replaced.error == "e"


class _NoArgTool(BaseTool):
    name: str = "no_args"
    description: str = "Returns a constant."

    async def execute(self) -> ToolResult:
        return ToolResult(output="done")


@pytest.mark.asyncio
async def test_collection_accepts_missing_input():
    result = await ToolCollection(_NoArgTool()).execute(name="no_args")
    assert str(result) == "done"


@pytest.mark.asyncio
async def test_collection_unknown_tool():
    result = await ToolCollection().execute(name="nope", tool_input={})
    assert result.error == "Tool nope is invalid"


def test_lazy_exports_resolve():
    for name in app.tool.__all__:
        assert getattr(app.tool, name).__name__ == name
    with pytest.raises(AttributeError):
        getattr(app.tool, "DoesNotExist")


def test_importing_app_tool_is_lightweight():
    code = (
        "import sys\n"
        "from app.tool import Terminate, ToolCollection, BaseTool\n"
        "heavy = [m for m in ('browser_use', 'playwright', 'crawl4ai',"
        " 'googlesearch', 'duckduckgo_search', 'daytona') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""


def test_all_tool_modules_import():
    failures = {}
    for module in pkgutil.walk_packages(app.tool.__path__, "app.tool."):
        try:
            importlib.import_module(module.name)
        except Exception as e:  # pragma: no cover - reported below
            failures[module.name] = repr(e)
    assert not failures
