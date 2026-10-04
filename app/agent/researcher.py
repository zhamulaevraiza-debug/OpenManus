from typing import List, Optional

from pydantic import Field, model_validator

from app.agent.browser import BrowserContextHelper
from app.agent.toolcall import ToolCallAgent
from app.context import get_workspace
from app.prompt.researcher import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.crawl4ai import Crawl4aiTool
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection
from app.tool.web_search import WebSearch


class ResearcherAgent(ToolCallAgent):
    """Web research agent: searches several sources, reads them, cross-checks facts
    and reports findings with cited URLs (optionally as a report in the workspace)."""

    name: str = "researcher"
    description: str = (
        "Researches topics on the web across multiple sources, verifies facts and "
        "writes well-sourced findings or reports with cited URLs"
    )

    # Formatted per instance so that each run sees its own workspace
    system_prompt: str = Field(
        default_factory=lambda: SYSTEM_PROMPT.format(directory=get_workspace())
    )
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 12000
    max_steps: int = 20

    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            WebSearch(),
            Crawl4aiTool(),
            BrowserUseTool(),
            StrReplaceEditor(),
            Terminate(),
        )
    )
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    browser_context_helper: Optional[BrowserContextHelper] = None

    @model_validator(mode="after")
    def initialize_helper(self) -> "ResearcherAgent":
        self.browser_context_helper = BrowserContextHelper(self)
        return self

    async def think(self) -> bool:
        """Decide the next action, with the browser state while browsing."""
        original_prompt = self.next_step_prompt
        self.next_step_prompt = await self.browser_context_helper.next_step_prompt_for(
            original_prompt
        )
        try:
            return await super().think()
        finally:
            self.next_step_prompt = original_prompt
