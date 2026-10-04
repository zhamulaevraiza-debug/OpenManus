import asyncio

from app.context import current_run
from app.tool.base import BaseTool


NO_HUMAN_AVAILABLE = (
    "No human is available to answer questions in this session. "
    "Continue with your best judgement and state your assumptions."
)


class AskHuman(BaseTool):
    """Add a tool to ask human for help."""

    name: str = "ask_human"
    description: str = "Use this tool to ask human for help."
    parameters: dict = {
        "type": "object",
        "properties": {
            "inquire": {
                "type": "string",
                "description": "The question you want to ask human.",
            }
        },
        "required": ["inquire"],
    }

    async def execute(self, inquire: str) -> str:
        """Ask the user ``inquire`` and return the answer.

        Inside a web run the question is delegated to the run's human-input provider
        (the UI); on the CLI it is read from the terminal without blocking the loop.
        """
        ctx = current_run()
        if ctx is not None:
            if ctx.ask_human is None:
                return NO_HUMAN_AVAILABLE
            answer = await ctx.ask_human(inquire)
            return (answer or "").strip()
        answer = await asyncio.to_thread(input, f"Bot: {inquire}\n\nYou: ")
        return answer.strip()
