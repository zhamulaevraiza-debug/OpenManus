"""Conversation history passed to runs: ``[{"role": "user"|"assistant", "content": str}]``."""

from typing import Dict, Iterable, List, Optional

from app.schema import Message
from app.utils.text import truncate


MAX_HISTORY_TURNS = 10
MAX_TURN_CHARS = 4000

History = List[Dict[str, str]]


def normalize_history(history: Optional[Iterable[dict]]) -> History:
    """Keep valid user/assistant turns (last ``MAX_HISTORY_TURNS``, each truncated)."""
    turns: History = []
    for turn in history or []:
        role, content = turn.get("role"), turn.get("content")
        if (
            role in ("user", "assistant")
            and isinstance(content, str)
            and content.strip()
        ):
            turns.append({"role": role, "content": truncate(content, MAX_TURN_CHARS)})
    return turns[-MAX_HISTORY_TURNS:]


def history_messages(history: History) -> List[Message]:
    """History turns as chat messages (to seed an LLM call or an agent's memory)."""
    return [
        (
            Message.user_message(turn["content"])
            if turn["role"] == "user"
            else Message.assistant_message(turn["content"])
        )
        for turn in history
    ]


def format_history(history: History, max_chars: int = 6000) -> str:
    """History as a ``Conversation so far`` prompt block ("" when there is none)."""
    if not history:
        return ""
    text = "\n\n".join(
        f"{'User' if turn['role'] == 'user' else 'Assistant'}: {turn['content']}"
        for turn in history
    )
    if len(text) > max_chars:
        text = "…\n" + text[-max_chars:]
    return f"Conversation so far:\n{text}\n\n"
