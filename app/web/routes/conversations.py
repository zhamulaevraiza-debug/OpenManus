"""Conversations, messages, retries and Markdown export."""

from __future__ import annotations

import asyncio
import re
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.web import files
from app.web.accounts import delete_conversations
from app.web.deps import Services, current_user, get_db, get_services, load_conversation
from app.web.models import Conversation, Message, Run, User, utcnow
from app.web.modes import AUTO, is_known_mode, validate_mode
from app.web.runs import RunRejected
from app.web.schemas import (
    ConversationCreate,
    ConversationUpdate,
    MessageCreate,
    conversation_out,
    iso,
    message_out,
    run_out,
)


router = APIRouter(prefix="/conversations", tags=["conversations"])

MAX_CONVERSATIONS = 1000
MAX_TITLE_CHARS = 60
PREVIEW_SQL_CHARS = 400


def _auto_title(content: str) -> str:
    """Conversation title derived from the first message (at most 60 chars)."""
    line = next((line for line in content.splitlines() if line.strip()), "")
    title = " ".join(line.split())
    if len(title) <= MAX_TITLE_CHARS:
        return title
    cut = title[: MAX_TITLE_CHARS - 1]
    if " " in cut[MAX_TITLE_CHARS // 2 :]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:-") + "…"


def _check_mode(mode: str) -> str:
    try:
        return validate_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


async def _conversation_view(
    session: AsyncSession, services: Services, conversation: Conversation
) -> dict:
    preview = await session.scalar(
        select(func.substr(Message.content, 1, PREVIEW_SQL_CHARS))
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return conversation_out(
        conversation, preview, services.runs.active_run_id(conversation.id)
    )


def _run_view(services: Services, run: Run) -> dict:
    return run_out(run, services.runs.live_seq(run.id))


@router.get("")
async def list_conversations(
    q: Optional[str] = Query(None, max_length=200),
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    preview = (
        select(func.substr(Message.content, 1, PREVIEW_SQL_CHARS))
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    statement = select(Conversation, preview).where(Conversation.user_id == user.id)
    if q and q.strip():
        needle = q.strip()
        statement = statement.where(
            or_(
                services.db.contains_text(Conversation.title, needle),
                exists().where(
                    Message.conversation_id == Conversation.id,
                    services.db.contains_text(Message.content, needle),
                ),
            )
        )
    statement = statement.order_by(
        Conversation.pinned.desc(), Conversation.updated_at.desc()
    ).limit(MAX_CONVERSATIONS)
    rows = (await session.execute(statement)).all()
    return [
        conversation_out(
            conversation, text, services.runs.active_run_id(conversation.id)
        )
        for conversation, text in rows
    ]


@router.post("")
async def create_conversation(
    body: ConversationCreate,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    mode = (body.mode or AUTO).strip()
    if not is_known_mode(mode):
        raise HTTPException(status_code=400, detail=f"Unknown mode '{mode}'")
    conversation = Conversation(
        user_id=user.id, title=(body.title or "").strip(), mode=mode
    )
    session.add(conversation)
    await session.commit()
    return conversation_out(conversation, None, None)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    )
    runs = list(
        await session.scalars(
            select(Run)
            .where(Run.conversation_id == conversation.id)
            .order_by(Run.created_at)
        )
    )
    preview = messages[-1].content if messages else None
    return {
        "conversation": conversation_out(
            conversation, preview, services.runs.active_run_id(conversation.id)
        ),
        "messages": [message_out(message) for message in messages],
        "runs": [_run_view(services, run) for run in runs],
    }


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    if body.title is not None:
        conversation.title = body.title.strip()
    if body.mode is not None:
        mode = body.mode.strip()
        if not is_known_mode(mode):
            raise HTTPException(status_code=400, detail=f"Unknown mode '{mode}'")
        conversation.mode = mode
    if body.pinned is not None:
        conversation.pinned = body.pinned
    await session.commit()
    return await _conversation_view(session, services, conversation)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    await services.runs.cancel_where(conversation_id=conversation.id)
    await delete_conversations(session, services.settings, [conversation])


def _export_filename(conversation: Conversation) -> str:
    base = re.sub(r"[^\w\-. ]+", "", conversation.title).strip(" .")
    base = re.sub(r"\s+", "-", base)[:60] or f"conversation-{conversation.id[:8]}"
    return f"{base}.md"


def _render_markdown(conversation: Conversation, messages: List[Message]) -> str:
    lines = [
        f"# {conversation.title or 'Conversation'}",
        "",
        f"_Exported from OpenManus · {iso(utcnow())}_",
    ]
    for message in messages:
        speaker = "User" if message.role == "user" else "Assistant"
        lines += ["", "---", "", f"**{speaker}** · {iso(message.created_at)}", ""]
        lines.append(message.content)
        if message.attachments:
            listed = ", ".join(f"`{path}`" for path in message.attachments)
            lines += ["", f"Attachments: {listed}"]
    return "\n".join(lines) + "\n"


@router.get("/{conversation_id}/export.md")
async def export_markdown(
    conversation_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    )
    filename = _export_filename(conversation)
    ascii_name = filename.encode("ascii", "ignore").decode() or "conversation.md"
    return Response(
        _render_markdown(conversation, messages),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''"
                f"{quote(filename)}"
            ),
            "Cache-Control": "no-store",
        },
    )


async def _attachments(
    services: Services, user: User, conversation: Conversation, paths: List[str]
) -> List[str]:
    """Validated workspace-relative attachment paths (posix, files only)."""
    workspace = services.settings.workspace_path(user.id, conversation.id)

    def check() -> List[str]:
        result = []
        root = workspace.resolve()
        for path in paths:
            try:
                target = files.resolve_path(workspace, path)
            except files.PathViolation as e:
                raise HTTPException(status_code=400, detail=f"{path}: {e}") from None
            if not target.is_file():
                raise HTTPException(
                    status_code=400, detail=f"Attachment not found: {path}"
                )
            relative = target.relative_to(root).as_posix()
            if relative not in result:
                result.append(relative)
        return result

    return await asyncio.to_thread(check) if paths else []


async def _start_run(
    services: Services,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    mode: str,
    message: Optional[Message],
    content: str,
    attachments: List[str],
) -> dict:
    """Persist (optionally) a new user message plus a queued run, then launch it.

    With ``message=None`` a new user message is created; otherwise the given
    existing message is re-run.
    """
    try:
        run_id = services.runs.reserve(user.id, conversation.id)
    except RunRejected as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    try:
        now = utcnow()
        if message is None:
            has_messages = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation.id)
            )
            if not has_messages and not conversation.title:
                conversation.title = _auto_title(content)
            message = Message(
                conversation_id=conversation.id,
                role="user",
                content=content,
                attachments=attachments,
                created_at=now,
            )
            session.add(message)
            await session.flush()
        message.run_id = run_id
        run = Run(
            id=run_id,
            conversation_id=conversation.id,
            user_id=user.id,
            request_message_id=message.id,
            mode=mode,
            created_at=now,
            last_seq=0,
        )
        session.add(run)
        conversation.updated_at = now
        await session.commit()
    except BaseException:
        services.runs.release(run_id)
        raise
    services.runs.launch(
        run_id,
        request=content,
        mode=mode,
        attachments=list(message.attachments or []),
        request_message_id=message.id,
    )
    return {"message": message_out(message), "run": _run_view(services, run)}


@router.post("/{conversation_id}/messages")
async def post_message(
    conversation_id: str,
    body: MessageCreate,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="The message is empty")
    mode = _check_mode(body.mode or conversation.mode)
    attachments = await _attachments(services, user, conversation, body.attachments)
    if body.mode:
        conversation.mode = mode
    return await _start_run(
        services, session, user, conversation, mode, None, body.content, attachments
    )


@router.post("/{conversation_id}/retry")
async def retry(
    conversation_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    message = await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Nothing to retry")
    mode = _check_mode(conversation.mode)
    return await _start_run(
        services,
        session,
        user,
        conversation,
        mode,
        message,
        message.content,
        list(message.attachments or []),
    )
