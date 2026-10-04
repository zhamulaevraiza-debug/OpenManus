"""Turning the work of agents into the final, user-facing answer."""

from typing import Callable, List, Optional

from app.agent.base import BaseAgent
from app.context import current_run, emit
from app.flow.history import History, format_history
from app.llm import LLM
from app.prompt.answer import FINAL_ANSWER_SYSTEM_PROMPT, FINAL_ANSWER_USER_PROMPT
from app.schema import Message
from app.utils.text import truncate


RECORD_MAX_CHARS = 24000  # work record given to the final-answer model
RECORD_ITEM_MAX_CHARS = 2000  # per thought / tool result in the record
ARGUMENTS_MAX_CHARS = 500  # per tool call in the record

# Assistant messages produced by error handling rather than by the model.
_ERROR_REPLY_PREFIXES = ("Error encountered while processing", "Maximum token limit")


def answer_delta_sink() -> Optional[Callable[[str], None]]:
    """Callback streaming answer chunks as ``answer.delta`` events during web runs.

    Returns None without an active run, so that the CLI prints the stream instead.
    """
    if current_run() is None:
        return None
    return lambda chunk: emit("answer.delta", content=chunk)


def agent_transcript(agent: BaseAgent, skip: int = 0) -> str:
    """Compact text record of an agent's work: thoughts, tool calls and results.

    Args:
        agent: The agent after its run.
        skip: Number of leading memory messages to ignore (seeded history).
    """
    lines: List[str] = []
    for message in agent.memory.messages[skip:]:
        if message.role == "assistant":
            if message.content:
                lines.append(
                    f"Agent: {truncate(message.content, RECORD_ITEM_MAX_CHARS)}"
                )
            for call in message.tool_calls or []:
                arguments = truncate(call.function.arguments or "", ARGUMENTS_MAX_CHARS)
                lines.append(f"Tool call `{call.function.name}`: {arguments}")
        elif message.role == "tool" and message.content:
            lines.append(
                f"Tool result `{message.name}`: "
                f"{truncate(message.content, RECORD_ITEM_MAX_CHARS)}"
            )
    record = "\n\n".join(lines)
    if len(record) > RECORD_MAX_CHARS:
        record = "…(earlier work omitted)\n" + record[-RECORD_MAX_CHARS:]
    return record


def last_agent_reply(agent: BaseAgent, skip: int = 0) -> str:
    """The agent's last own message text ("" if none), e.g. its result summary."""
    for message in reversed(agent.memory.messages[skip:]):
        if (
            message.role == "assistant"
            and message.content
            and not message.content.startswith(_ERROR_REPLY_PREFIXES)
        ):
            return message.content.strip()
    return ""


async def generate_final_answer(
    request: str,
    record: str,
    history: Optional[History] = None,
    llm: Optional[LLM] = None,
) -> str:
    """Write the user-facing Markdown answer (in the user's language) from a work record.

    The answer is streamed as ``answer.delta`` events while a web run is active.
    """
    llm = llm or LLM()
    prompt = FINAL_ANSWER_USER_PROMPT.format(
        history=format_history(history or []),
        request=request,
        record=record or "(no work was recorded)",
    )
    return await llm.ask(
        [Message.user_message(prompt)],
        system_msgs=[Message.system_message(FINAL_ANSWER_SYSTEM_PROMPT)],
        on_delta=answer_delta_sink(),
    )
