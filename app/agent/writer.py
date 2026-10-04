from typing import List

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.context import get_workspace
from app.prompt.writer import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.tool.python_execute import PythonExecute
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection
from app.tool.web_search import WebSearch


class WriterAgent(ToolCallAgent):
    """Writing agent producing polished long-form documents (Markdown, DOCX, HTML)
    saved to the workspace."""

    name: str = "writer"
    description: str = (
        "Writes polished long-form documents (reports, articles, proposals, docs) "
        "and saves them to the workspace as Markdown, Word (DOCX) or HTML"
    )

    # Formatted per instance so that each run sees its own workspace
    system_prompt: str = Field(
        default_factory=lambda: SYSTEM_PROMPT.format(directory=get_workspace())
    )
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 10000
    max_steps: int = 20

    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            StrReplaceEditor(),
            PythonExecute(),
            WebSearch(),
            Terminate(),
        )
    )
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])
