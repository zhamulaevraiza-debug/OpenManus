import json
from typing import TYPE_CHECKING, Optional

from pydantic import Field, model_validator

from app.agent.toolcall import ToolCallAgent
from app.logger import logger
from app.prompt.browser import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import Message, ToolChoice
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection


# Avoid circular import if BrowserAgent needs BrowserContextHelper
if TYPE_CHECKING:
    from app.agent.base import BaseAgent  # Or wherever memory is defined


# Names of the local (browser_use) and Daytona sandbox browser tools. Using names
# instead of the classes keeps the Daytona stack out of the import graph.
BROWSER_TOOL_NAME = "browser_use"
SANDBOX_BROWSER_TOOL_NAME = "sandbox_browser"
BROWSER_TOOL_NAMES = (BROWSER_TOOL_NAME, SANDBOX_BROWSER_TOOL_NAME)


class BrowserContextHelper:
    """Builds the browser-state prompt (with a screenshot) for browsing agents."""

    def __init__(self, agent: "BaseAgent"):
        self.agent = agent
        self._current_base64_image: Optional[str] = None

    def _browser_tool(self):
        for name in BROWSER_TOOL_NAMES:
            tool = self.agent.available_tools.get_tool(name)
            if tool is not None:
                return tool
        return None

    def browser_recently_used(self, lookback: int = 3) -> bool:
        """Whether one of the last ``lookback`` messages called a browser tool."""
        return any(
            call.function.name in BROWSER_TOOL_NAMES
            for message in self.agent.memory.messages[-lookback:]
            for call in message.tool_calls or []
        )

    def _latest_result_has_image(self) -> bool:
        """Whether the latest memory message is a tool result with a screenshot."""
        messages = self.agent.memory.messages
        return bool(
            messages and messages[-1].role == "tool" and messages[-1].base64_image
        )

    async def get_browser_state(self) -> Optional[dict]:
        browser_tool = self._browser_tool()
        if not browser_tool or not hasattr(browser_tool, "get_current_state"):
            logger.warning("BrowserUseTool not found or doesn't have get_current_state")
            return None
        try:
            result = await browser_tool.get_current_state()
            if result.error:
                logger.debug(f"Browser state error: {result.error}")
                return None
            if hasattr(result, "base64_image") and result.base64_image:
                self._current_base64_image = result.base64_image
            else:
                self._current_base64_image = None
            return json.loads(result.output)
        except Exception as e:
            logger.debug(f"Failed to get browser state: {str(e)}")
            return None

    async def format_next_step_prompt(self) -> str:
        """Gets browser state and formats the browser prompt."""
        browser_state = await self.get_browser_state()
        url_info, tabs_info, content_above_info, content_below_info = "", "", "", ""
        results_info = ""  # Or get from agent if needed elsewhere

        if browser_state and not browser_state.get("error"):
            url_info = f"\n   URL: {browser_state.get('url', 'N/A')}\n   Title: {browser_state.get('title', 'N/A')}"
            tabs = browser_state.get("tabs", [])
            if tabs:
                tabs_info = f"\n   {len(tabs)} tab(s) available"
            scroll_info = browser_state.get("scroll_info") or browser_state
            pixels_above = scroll_info.get("pixels_above", 0)
            pixels_below = scroll_info.get("pixels_below", 0)
            if pixels_above > 0:
                content_above_info = f" ({pixels_above} pixels)"
            if pixels_below > 0:
                content_below_info = f" ({pixels_below} pixels)"

            # Browser actions already return a screenshot with their result; add
            # the state screenshot only when the model has not just seen the page.
            if self._current_base64_image and not self._latest_result_has_image():
                image_message = Message.user_message(
                    content="Current browser screenshot:",
                    base64_image=self._current_base64_image,
                )
                self.agent.memory.add_message(image_message)
            self._current_base64_image = None  # Consume the image

        return NEXT_STEP_PROMPT.format(
            url_placeholder=url_info,
            tabs_placeholder=tabs_info,
            content_above_placeholder=content_above_info,
            content_below_placeholder=content_below_info,
            results_placeholder=results_info,
        )

    async def next_step_prompt_for(
        self, default_prompt: Optional[str]
    ) -> Optional[str]:
        """The browser-state prompt while the browser is in use, else ``default_prompt``."""
        if self.browser_recently_used():
            return await self.format_next_step_prompt()
        return default_prompt


class BrowserAgent(ToolCallAgent):
    """
    A browser agent that uses the browser_use library to control a browser.

    This agent can navigate web pages, interact with elements, fill forms,
    extract content, and perform other browser-based actions to accomplish tasks.
    """

    name: str = "browser"
    description: str = "A browser agent that can control a browser to accomplish tasks"

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 10000
    max_steps: int = 20

    # Configure the available tools
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(BrowserUseTool(), Terminate())
    )

    # Use Auto for tool choice to allow both tool usage and free-form responses
    tool_choices: ToolChoice = ToolChoice.AUTO
    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])

    browser_context_helper: Optional[BrowserContextHelper] = None

    @model_validator(mode="after")
    def initialize_helper(self) -> "BrowserAgent":
        self.browser_context_helper = BrowserContextHelper(self)
        return self

    async def think(self) -> bool:
        """Process current state and decide next actions using tools, with browser state info added"""
        self.next_step_prompt = (
            await self.browser_context_helper.format_next_step_prompt()
        )
        return await super().think()
