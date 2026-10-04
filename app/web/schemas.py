"""Request bodies and JSON serialization of the web API (SPEC section 2 shapes)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.web import models


MAX_MESSAGE_CHARS = 20000
MAX_ATTACHMENTS = 20
PREVIEW_CHARS = 160


def iso(value: Optional[datetime]) -> Optional[str]:
    """ISO-8601 UTC with millisecond precision and a ``Z`` suffix."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


# ------------------------------------------------------------------ requests


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(_Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ChangePassword(_Body):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=1024)


class ConversationCreate(_Body):
    title: Optional[str] = Field(None, max_length=200)
    mode: Optional[str] = Field(None, min_length=1, max_length=64)


class ConversationUpdate(_Body):
    title: Optional[str] = Field(None, max_length=200)
    mode: Optional[str] = Field(None, min_length=1, max_length=64)
    pinned: Optional[bool] = None


class MessageCreate(_Body):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    mode: Optional[str] = Field(None, min_length=1, max_length=64)
    attachments: List[Annotated[str, Field(min_length=1, max_length=1024)]] = Field(
        default_factory=list, max_length=MAX_ATTACHMENTS
    )


class Answer(_Body):
    question_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(max_length=MAX_MESSAGE_CHARS)


class UserCreate(_Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=1024)
    is_admin: bool = False


class UserUpdate(_Body):
    is_admin: Optional[bool] = None
    disabled: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=8, max_length=1024)


class TestLLM(_Body):
    which: Literal["llm", "llm_vision"] = "llm"


# ----------------------------------------------------------------- responses


def user_out(user: models.User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": bool(user.is_admin),
        "disabled": bool(user.disabled),
        "created_at": iso(user.created_at),
    }


def conversation_out(
    conversation: models.Conversation,
    last_message_preview: Optional[str],
    active_run_id: Optional[str],
) -> Dict[str, Any]:
    preview = None
    if last_message_preview:
        preview = " ".join(last_message_preview.split())[:PREVIEW_CHARS] or None
    return {
        "id": conversation.id,
        "title": conversation.title,
        "mode": conversation.mode,
        "pinned": bool(conversation.pinned),
        "created_at": iso(conversation.created_at),
        "updated_at": iso(conversation.updated_at),
        "last_message_preview": preview,
        "active_run_id": active_run_id,
    }


def message_out(message: models.Message) -> Dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "run_id": message.run_id,
        "attachments": list(message.attachments or []),
        "created_at": iso(message.created_at),
    }


def run_out(run: models.Run, live_seq: Optional[int] = None) -> Dict[str, Any]:
    usage = run.usage or None
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "mode": run.mode,
        "status": run.status,
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "error": run.error,
        "usage": (
            {
                "input_tokens": int(usage.get("input_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
            }
            if usage
            else None
        ),
        "pending_question": run.pending_question or None,
        "last_seq": max(run.last_seq or 0, live_seq or 0),
    }


def event_out(event: models.RunEvent) -> Dict[str, Any]:
    return {
        "seq": event.seq,
        "run_id": event.run_id,
        "type": event.type,
        "ts": iso(event.ts),
        "data": event.data or {},
    }
