"""BrowserUseTool against a local page: navigation, state, SSRF and browser slots."""

import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.tool import browser_use_tool
from app.tool.browser_use_tool import BrowserUseTool, _browser_config_kwargs
from app.tool.web_search import SearchResponse


pytestmark = pytest.mark.asyncio

PAGE = """<html><head><title>Smoke Page</title></head>
<body><h1>Hello browser</h1><a href="/second">Second page</a>
<input id="q" placeholder="query"></body></html>"""


@pytest.fixture
def site(local_site):
    local_site.html("/", PAGE)
    local_site.html(
        "/second", "<html><head><title>Second</title></head><body>2</body></html>"
    )
    return local_site


async def test_navigation_and_state(allow_private_network, run_ctx, site):
    tool = BrowserUseTool()
    try:
        result = await tool.execute(action="go_to_url", url=site.url("/"))
        assert result.error is None, result.error
        assert result.output == f"Navigated to {site.url('/')}"
        # View-changing actions return a viewport screenshot with their result.
        shot = Image.open(io.BytesIO(base64.b64decode(result.base64_image)))
        assert shot.format == "JPEG"

        state = await tool.get_current_state()
        assert state.error is None, state.error
        info = json.loads(state.output)
        assert info["title"] == "Smoke Page"
        assert "Second page" in info["interactive_elements"]

        image = Image.open(io.BytesIO(base64.b64decode(state.base64_image)))
        assert image.format == "JPEG"
        viewport = tool.context.config.browser_window_size
        assert image.size == (viewport["width"], viewport["height"])

        link_index = next(
            index
            for index, element in (await tool.context.get_selector_map()).items()
            if element.tag_name == "a"
        )
        clicked = await tool.execute(action="click_element", index=link_index)
        assert clicked.error is None, clicked.error
        page = await tool.context.get_current_page()
        assert page.url == site.url("/second")
    finally:
        await tool.cleanup()
    assert tool.browser is None and tool.context is None


async def test_private_and_file_urls_are_blocked(block_private_network, run_ctx, site):
    tool = BrowserUseTool()
    try:
        for action in ("go_to_url", "open_tab"):
            result = await tool.execute(action=action, url=site.url("/"))
            assert result.error and "private or reserved address" in result.error
            result = await tool.execute(action=action, url="file:///etc/passwd")
            assert result.error and "only http and https" in result.error
        assert site.requests == []
    finally:
        await tool.cleanup()


async def test_extract_content_uses_llm(allow_private_network, run_ctx, site):
    calls = []

    class FakeLLM:
        async def ask_tool(self, messages, tools, tool_choice):
            calls.append(messages[0]["content"])
            arguments = json.dumps({"extracted_content": {"text": "Hello browser"}})
            return SimpleNamespace(
                tool_calls=[
                    SimpleNamespace(function=SimpleNamespace(arguments=arguments))
                ]
            )

    tool = BrowserUseTool()
    object.__setattr__(tool, "llm", FakeLLM())
    try:
        await tool.execute(action="go_to_url", url=site.url("/"))
        result = await tool.execute(action="extract_content", goal="greeting")
        assert "Hello browser" in result.output
        assert "Hello browser" in calls[0]
    finally:
        await tool.cleanup()


async def test_web_search_action_returns_search_errors(run_ctx):
    tool = BrowserUseTool()

    async def failing_search(**kwargs):
        return SearchResponse(query=kwargs["query"], error="all engines failed")

    object.__setattr__(tool.web_search_tool, "execute", failing_search)
    try:
        result = await tool.execute(action="web_search", query="anything")
        assert result.error == "all engines failed"
    finally:
        await tool.cleanup()


async def test_browser_slots_limit_concurrent_browsers(
    allow_private_network, monkeypatch, run_ctx, site
):
    monkeypatch.setenv("OPENMANUS_MAX_BROWSERS", "1")
    monkeypatch.setattr(browser_use_tool, "BROWSER_SLOT_TIMEOUT_SECONDS", 0.5)
    first, second = BrowserUseTool(), BrowserUseTool()
    try:
        assert (
            await first.execute(action="go_to_url", url=site.url("/"))
        ).error is None
        blocked = await second.execute(action="go_to_url", url=site.url("/"))
        assert blocked.error and "All browser slots are busy" in blocked.error

        await first.cleanup()
        assert (
            await second.execute(action="go_to_url", url=site.url("/"))
        ).error is None
    finally:
        await first.cleanup()
        await second.cleanup()


async def test_wait_is_bounded(run_ctx, monkeypatch):
    monkeypatch.setattr(browser_use_tool, "MAX_WAIT_SECONDS", 0)
    tool = BrowserUseTool()
    try:
        result = await tool.execute(action="wait", seconds=3600)
        assert result.output == "Waited for 0 seconds"
    finally:
        await tool.cleanup()


async def test_browser_config_defaults_to_headless(monkeypatch):
    monkeypatch.setattr(
        browser_use_tool.config._config, "browser_config", None, raising=False
    )
    kwargs = _browser_config_kwargs()
    assert kwargs["headless"] is True
    assert kwargs["disable_security"] is False


async def test_tool_has_no_finalizer():
    assert "__del__" not in BrowserUseTool.__dict__
