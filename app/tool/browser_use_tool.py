import asyncio
import base64
import json
import os
import weakref
from typing import Any, Dict, Generic, Optional, TypeVar

from browser_use import Browser as BrowserUseBrowser
from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
from pydantic import Field, PrivateAttr, field_validator
from pydantic_core.core_schema import ValidationInfo

from app.config import config
from app.llm import LLM
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.net_guard import (
    UnsafeURLError,
    check_url,
    enforce_browser_policy,
    guard_browser_context,
)
from app.tool.web_search import WebSearch


_BROWSER_DESCRIPTION = """\
A powerful browser automation tool that allows interaction with web pages through various actions.
* This tool provides commands for controlling a browser session, navigating web pages, and extracting information
* It maintains state across calls, keeping the browser session alive until explicitly closed
* Use this when you need to browse websites, fill forms, click buttons, extract content, or perform web searches
* Each action requires specific parameters as defined in the tool's dependencies

Key capabilities include:
* Navigation: Go to specific URLs, go back, search the web, or refresh pages
* Interaction: Click elements, input text, select from dropdowns, send keyboard commands
* Scrolling: Scroll up/down by pixel amount or scroll to specific text
* Content extraction: Extract and analyze content from web pages based on specific goals
* Tab management: Switch between tabs, open new tabs, or close tabs

Note: When using element indices, refer to the numbered elements shown in the current browser state.
"""

Context = TypeVar("Context")

MAX_BROWSERS_ENV = "OPENMANUS_MAX_BROWSERS"
DEFAULT_MAX_BROWSERS = 3
BROWSER_SLOT_TIMEOUT_SECONDS = 120
SCREENSHOT_JPEG_QUALITY = 70
MAX_WAIT_SECONDS = 30
# Actions that change what the page shows: their results carry a viewport screenshot
# (shown to the user as the tool result and seen by the model).
VIEW_ACTIONS = frozenset(
    {
        "go_to_url",
        "go_back",
        "refresh",
        "web_search",
        "click_element",
        "input_text",
        "scroll_down",
        "scroll_up",
        "scroll_to_text",
        "send_keys",
        "select_dropdown_option",
        "switch_tab",
        "open_tab",
        "close_tab",
        "wait",
    }
)

# One semaphore per event loop limits how many Chromium instances run at once.
_browser_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _max_browsers() -> int:
    try:
        return max(1, int(os.environ.get(MAX_BROWSERS_ENV, DEFAULT_MAX_BROWSERS)))
    except ValueError:
        return DEFAULT_MAX_BROWSERS


def _browser_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _browser_slots.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_max_browsers())
        _browser_slots[loop] = semaphore
    return semaphore


def _browser_config_kwargs() -> Dict[str, Any]:
    """BrowserConfig arguments from ``[browser]`` settings (headless by default)."""
    settings = config.browser_config
    kwargs: Dict[str, Any] = {
        "headless": getattr(settings, "headless", True),
        "disable_security": getattr(settings, "disable_security", False),
    }
    if settings is None:
        return kwargs

    if settings.proxy and settings.proxy.server:
        from browser_use.browser.browser import ProxySettings

        kwargs["proxy"] = ProxySettings(
            server=settings.proxy.server,
            username=settings.proxy.username,
            password=settings.proxy.password,
        )
    for attr in ("extra_chromium_args", "chrome_instance_path", "wss_url", "cdp_url"):
        value = getattr(settings, attr, None)
        if value is not None and (not isinstance(value, list) or value):
            kwargs[attr] = value
    return kwargs


class BrowserUseTool(BaseTool, Generic[Context]):
    name: str = "browser_use"
    description: str = _BROWSER_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "go_to_url",
                    "click_element",
                    "input_text",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "send_keys",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "go_back",
                    "web_search",
                    "wait",
                    "extract_content",
                    "switch_tab",
                    "open_tab",
                    "close_tab",
                ],
                "description": "The browser action to perform",
            },
            "url": {
                "type": "string",
                "description": "URL for 'go_to_url' or 'open_tab' actions",
            },
            "index": {
                "type": "integer",
                "description": "Element index for 'click_element', 'input_text', 'get_dropdown_options', or 'select_dropdown_option' actions",
            },
            "text": {
                "type": "string",
                "description": "Text for 'input_text', 'scroll_to_text', or 'select_dropdown_option' actions",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "Pixels to scroll (positive for down, negative for up) for 'scroll_down' or 'scroll_up' actions",
            },
            "tab_id": {
                "type": "integer",
                "description": "Tab ID for 'switch_tab' action",
            },
            "query": {
                "type": "string",
                "description": "Search query for 'web_search' action",
            },
            "goal": {
                "type": "string",
                "description": "Extraction goal for 'extract_content' action",
            },
            "keys": {
                "type": "string",
                "description": "Keys to send for 'send_keys' action",
            },
            "seconds": {
                "type": "integer",
                "description": "Seconds to wait for 'wait' action",
            },
        },
        "required": ["action"],
        "dependencies": {
            "go_to_url": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "switch_tab": ["tab_id"],
            "open_tab": ["url"],
            "scroll_down": ["scroll_amount"],
            "scroll_up": ["scroll_amount"],
            "scroll_to_text": ["text"],
            "send_keys": ["keys"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "go_back": [],
            "web_search": ["query"],
            "wait": ["seconds"],
            "extract_content": ["goal"],
        },
    }

    lock: asyncio.Lock = Field(default_factory=asyncio.Lock)
    browser: Optional[BrowserUseBrowser] = Field(default=None, exclude=True)
    context: Optional[BrowserContext] = Field(default=None, exclude=True)
    dom_service: Optional[DomService] = Field(default=None, exclude=True)
    web_search_tool: WebSearch = Field(default_factory=WebSearch, exclude=True)

    # Context for generic functionality
    tool_context: Optional[Context] = Field(default=None, exclude=True)

    # Used by extract_content; the default LLM is created on first use.
    llm: Optional[LLM] = Field(default=None)

    _slot: Optional[asyncio.Semaphore] = PrivateAttr(default=None)

    @field_validator("parameters", mode="before")
    def validate_parameters(cls, v: dict, info: ValidationInfo) -> dict:
        if not v:
            raise ValueError("Parameters cannot be empty")
        return v

    async def _acquire_slot(self) -> None:
        semaphore = _browser_semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), BROWSER_SLOT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "All browser slots are busy (OPENMANUS_MAX_BROWSERS="
                f"{_max_browsers()}); try again later"
            ) from None
        self._slot = semaphore

    def _release_slot(self) -> None:
        slot, self._slot = self._slot, None
        if slot is not None:
            slot.release()

    async def _ensure_browser_initialized(self) -> BrowserContext:
        """Ensure browser and context are initialized (and guarded against SSRF)."""
        if self.browser is None:
            await self._acquire_slot()
            try:
                self.browser = BrowserUseBrowser(
                    BrowserConfig(**_browser_config_kwargs())
                )
            except BaseException:
                self._release_slot()
                raise

        if self.context is None:
            try:
                disable_security = getattr(
                    config.browser_config, "disable_security", False
                )
                self.context = await self.browser.new_context(
                    BrowserContextConfig(disable_security=disable_security)
                )
                session = await self.context.get_session()
                await guard_browser_context(session.context)
                self.dom_service = DomService(await self.context.get_current_page())
            except BaseException:
                await self._close_browser()
                raise

        return self.context

    async def _enforce_network_policy(self) -> Optional[ToolResult]:
        """Reset pages that reached a private address (e.g. through a redirect)."""
        if self.context is None or self.context.session is None:
            return None
        violations = await enforce_browser_policy(self.context.session.context)
        if not violations:
            return None
        return ToolResult(
            error="Blocked access to a private or local network address: "
            + ", ".join(violations)
        )

    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        tab_id: Optional[int] = None,
        query: Optional[str] = None,
        goal: Optional[str] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        """
        Execute a specified browser action.

        Args:
            action: The browser action to perform
            url: URL for navigation or new tab
            index: Element index for click or input actions
            text: Text for input action or search query
            scroll_amount: Pixels to scroll for scroll action
            tab_id: Tab ID for switch_tab action
            query: Search query for Google search
            goal: Extraction goal for content extraction
            keys: Keys to send for keyboard actions
            seconds: Seconds to wait
            **kwargs: Additional arguments

        Returns:
            ToolResult with the action's output or error
        """
        async with self.lock:
            result = await self._run_action(
                action,
                url=url,
                index=index,
                text=text,
                scroll_amount=scroll_amount,
                tab_id=tab_id,
                query=query,
                goal=goal,
                keys=keys,
                seconds=seconds,
            )
            blocked = await self._enforce_network_policy()
            if blocked is not None:
                return blocked
            if action in VIEW_ACTIONS and not result.error:
                result = result.replace(base64_image=await self._try_screenshot())
            return result

    @staticmethod
    async def _viewport_screenshot(context: BrowserContext) -> str:
        """Base64 JPEG of the visible viewport of the context's current page."""
        page = await context.get_current_page()
        await page.bring_to_front()
        await page.wait_for_load_state()
        screenshot = await page.screenshot(
            full_page=False,
            animations="disabled",
            type="jpeg",
            quality=SCREENSHOT_JPEG_QUALITY,
        )
        return base64.b64encode(screenshot).decode("utf-8")

    async def _try_screenshot(self) -> Optional[str]:
        """Viewport screenshot after an action (None when it cannot be taken)."""
        try:
            return await self._viewport_screenshot(self.context)
        except Exception as e:
            logger.debug(f"Browser screenshot failed: {e}")
            return None

    async def _run_action(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        tab_id: Optional[int] = None,
        query: Optional[str] = None,
        goal: Optional[str] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
    ) -> ToolResult:
        """Perform one browser action; errors are returned as ToolResult."""
        try:
            context = await self._ensure_browser_initialized()

            # Get max content length from config
            max_content_length = getattr(
                config.browser_config, "max_content_length", 2000
            )

            # Navigation actions
            if action == "go_to_url":
                if not url:
                    return ToolResult(error="URL is required for 'go_to_url' action")
                await check_url(url)
                page = await context.get_current_page()
                await page.goto(url)
                await page.wait_for_load_state()
                return ToolResult(output=f"Navigated to {url}")

            elif action == "go_back":
                await context.go_back()
                return ToolResult(output="Navigated back")

            elif action == "refresh":
                await context.refresh_page()
                return ToolResult(output="Refreshed current page")

            elif action == "web_search":
                if not query:
                    return ToolResult(error="Query is required for 'web_search' action")
                # Search, then open the first result in the browser
                search_response = await self.web_search_tool.execute(
                    query=query, fetch_content=True, num_results=1
                )
                if search_response.error or not search_response.results:
                    return search_response
                url_to_navigate = search_response.results[0].url
                await check_url(url_to_navigate)

                page = await context.get_current_page()
                await page.goto(url_to_navigate)
                await page.wait_for_load_state()

                return search_response

            # Element interaction actions
            elif action == "click_element":
                if index is None:
                    return ToolResult(
                        error="Index is required for 'click_element' action"
                    )
                element = await context.get_dom_element_by_index(index)
                if not element:
                    return ToolResult(error=f"Element with index {index} not found")
                download_path = await context._click_element_node(element)
                output = f"Clicked element at index {index}"
                if download_path:
                    output += f" - Downloaded file to {download_path}"
                return ToolResult(output=output)

            elif action == "input_text":
                if index is None or not text:
                    return ToolResult(
                        error="Index and text are required for 'input_text' action"
                    )
                element = await context.get_dom_element_by_index(index)
                if not element:
                    return ToolResult(error=f"Element with index {index} not found")
                await context._input_text_element_node(element, text)
                return ToolResult(
                    output=f"Input '{text}' into element at index {index}"
                )

            elif action == "scroll_down" or action == "scroll_up":
                direction = 1 if action == "scroll_down" else -1
                amount = (
                    scroll_amount
                    if scroll_amount is not None
                    else context.config.browser_window_size["height"]
                )
                await context.execute_javascript(
                    f"window.scrollBy(0, {direction * amount});"
                )
                return ToolResult(
                    output=f"Scrolled {'down' if direction > 0 else 'up'} by {amount} pixels"
                )

            elif action == "scroll_to_text":
                if not text:
                    return ToolResult(
                        error="Text is required for 'scroll_to_text' action"
                    )
                page = await context.get_current_page()
                try:
                    locator = page.get_by_text(text, exact=False)
                    await locator.scroll_into_view_if_needed()
                    return ToolResult(output=f"Scrolled to text: '{text}'")
                except Exception as e:
                    return ToolResult(error=f"Failed to scroll to text: {str(e)}")

            elif action == "send_keys":
                if not keys:
                    return ToolResult(error="Keys are required for 'send_keys' action")
                page = await context.get_current_page()
                await page.keyboard.press(keys)
                return ToolResult(output=f"Sent keys: {keys}")

            elif action == "get_dropdown_options":
                if index is None:
                    return ToolResult(
                        error="Index is required for 'get_dropdown_options' action"
                    )
                element = await context.get_dom_element_by_index(index)
                if not element:
                    return ToolResult(error=f"Element with index {index} not found")
                page = await context.get_current_page()
                options = await page.evaluate(
                    """
                    (xpath) => {
                        const select = document.evaluate(xpath, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (!select) return null;
                        return Array.from(select.options).map(opt => ({
                            text: opt.text,
                            value: opt.value,
                            index: opt.index
                        }));
                    }
                """,
                    element.xpath,
                )
                return ToolResult(output=f"Dropdown options: {options}")

            elif action == "select_dropdown_option":
                if index is None or not text:
                    return ToolResult(
                        error="Index and text are required for 'select_dropdown_option' action"
                    )
                element = await context.get_dom_element_by_index(index)
                if not element:
                    return ToolResult(error=f"Element with index {index} not found")
                page = await context.get_current_page()
                await page.select_option(element.xpath, label=text)
                return ToolResult(
                    output=f"Selected option '{text}' from dropdown at index {index}"
                )

            # Content extraction actions
            elif action == "extract_content":
                if not goal:
                    return ToolResult(
                        error="Goal is required for 'extract_content' action"
                    )

                blocked = await self._enforce_network_policy()
                if blocked is not None:
                    return blocked
                page = await context.get_current_page()
                import markdownify

                content = markdownify.markdownify(await page.content())

                prompt = f"""\
Your task is to extract the content of the page. You will be given a page and a goal, and you should extract all relevant information around this goal from the page. If the goal is vague, summarize the page. Respond in json format.
Extraction goal: {goal}

Page content:
{content[:max_content_length]}
"""
                messages = [{"role": "system", "content": prompt}]

                # Define extraction function schema
                extraction_function = {
                    "type": "function",
                    "function": {
                        "name": "extract_content",
                        "description": "Extract specific information from a webpage based on a goal",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "extracted_content": {
                                    "type": "object",
                                    "description": "The content extracted from the page according to the goal",
                                    "properties": {
                                        "text": {
                                            "type": "string",
                                            "description": "Text content extracted from the page",
                                        },
                                        "metadata": {
                                            "type": "object",
                                            "description": "Additional metadata about the extracted content",
                                            "properties": {
                                                "source": {
                                                    "type": "string",
                                                    "description": "Source of the extracted content",
                                                }
                                            },
                                        },
                                    },
                                }
                            },
                            "required": ["extracted_content"],
                        },
                    },
                }

                # Use LLM to extract content with required function calling
                if self.llm is None:
                    self.llm = LLM()
                response = await self.llm.ask_tool(
                    messages,
                    tools=[extraction_function],
                    tool_choice="required",
                )

                if response and response.tool_calls:
                    args = json.loads(response.tool_calls[0].function.arguments)
                    extracted_content = args.get("extracted_content", {})
                    return ToolResult(
                        output=f"Extracted from page:\n{extracted_content}\n"
                    )

                return ToolResult(output="No content was extracted from the page.")

            # Tab management actions
            elif action == "switch_tab":
                if tab_id is None:
                    return ToolResult(
                        error="Tab ID is required for 'switch_tab' action"
                    )
                await context.switch_to_tab(tab_id)
                page = await context.get_current_page()
                await page.wait_for_load_state()
                return ToolResult(output=f"Switched to tab {tab_id}")

            elif action == "open_tab":
                if not url:
                    return ToolResult(error="URL is required for 'open_tab' action")
                await check_url(url)
                await context.create_new_tab(url)
                return ToolResult(output=f"Opened new tab with {url}")

            elif action == "close_tab":
                await context.close_current_tab()
                return ToolResult(output="Closed current tab")

            # Utility actions
            elif action == "wait":
                seconds_to_wait = seconds if seconds is not None else 3
                seconds_to_wait = max(0, min(int(seconds_to_wait), MAX_WAIT_SECONDS))
                await asyncio.sleep(seconds_to_wait)
                return ToolResult(output=f"Waited for {seconds_to_wait} seconds")

            else:
                return ToolResult(error=f"Unknown action: {action}")

        except UnsafeURLError as e:
            return ToolResult(error=e.message)
        except Exception as e:
            return ToolResult(error=f"Browser action '{action}' failed: {str(e)}")

    async def get_current_state(
        self, context: Optional[BrowserContext] = None
    ) -> ToolResult:
        """
        Get the current browser state as a ToolResult.
        If context is not provided, uses self.context.
        The screenshot is a JPEG of the visible viewport.
        """
        async with self.lock:
            try:
                # Use provided context or fall back to self.context
                ctx = context or self.context
                if not ctx:
                    return ToolResult(error="Browser context not initialized")

                blocked = await self._enforce_network_policy()
                if blocked is not None:
                    return blocked

                state = await ctx.get_state()

                viewport_height = 0
                if hasattr(state, "viewport_info") and state.viewport_info:
                    viewport_height = state.viewport_info.height
                elif hasattr(ctx, "config") and hasattr(
                    ctx.config, "browser_window_size"
                ):
                    viewport_height = ctx.config.browser_window_size.get("height", 0)

                screenshot = await self._viewport_screenshot(ctx)

                # Build the state info with all required fields
                state_info = {
                    "url": state.url,
                    "title": state.title,
                    "tabs": [tab.model_dump() for tab in state.tabs],
                    "help": "[0], [1], [2], etc., represent clickable indices corresponding to the elements listed. Clicking on these indices will navigate to or interact with the respective content behind them.",
                    "interactive_elements": (
                        state.element_tree.clickable_elements_to_string()
                        if state.element_tree
                        else ""
                    ),
                    "scroll_info": {
                        "pixels_above": getattr(state, "pixels_above", 0),
                        "pixels_below": getattr(state, "pixels_below", 0),
                        "total_height": getattr(state, "pixels_above", 0)
                        + getattr(state, "pixels_below", 0)
                        + viewport_height,
                    },
                    "viewport_height": viewport_height,
                }

                return ToolResult(
                    output=json.dumps(state_info, indent=4, ensure_ascii=False),
                    base64_image=screenshot,
                )
            except Exception as e:
                return ToolResult(error=f"Failed to get browser state: {str(e)}")

    async def _close_browser(self) -> None:
        """Close context and browser (best effort) and free the browser slot."""
        try:
            if self.context is not None:
                try:
                    await self.context.close()
                except Exception as e:
                    logger.debug(f"Error closing browser context: {e}")
                self.context = None
                self.dom_service = None
            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception as e:
                    logger.debug(f"Error closing browser: {e}")
                self.browser = None
        finally:
            self._release_slot()

    async def cleanup(self):
        """Clean up browser resources."""
        async with self.lock:
            await self._close_browser()

    @classmethod
    def create_with_context(cls, context: Context) -> "BrowserUseTool[Context]":
        """Factory method to create a BrowserUseTool with a specific context."""
        tool = cls()
        tool.tool_context = context
        return tool
