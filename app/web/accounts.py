"""User account helpers: admin bootstrap and removal of user/conversation data."""

from __future__ import annotations

import asyncio
import os
import secrets
from typing import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logger import logger
from app.web import files
from app.web.db import Database
from app.web.models import Conversation, Run, User
from app.web.security import hash_password, validate_username
from app.web.settings import WebSettings


async def bootstrap_admin(db: Database, settings: WebSettings) -> None:
    """Create the initial administrator when the database has no users.

    The password comes from ``OPENMANUS_ADMIN_PASSWORD``; otherwise a random one is
    written to ``DATA_DIR/initial_admin_password.txt`` (mode 600). The password itself
    is never logged.
    """
    async with db.session() as session:
        if await session.scalar(select(func.count()).select_from(User)):
            return
        username = validate_username(settings.admin_username)
        password = settings.admin_password
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        session.add(
            User(
                username=username,
                password_hash=await hash_password(password),
                is_admin=True,
            )
        )
        await session.commit()
    if generated:
        path = settings.initial_password_file
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(password + "\n")
        os.chmod(path, 0o600)
        logger.warning(
            f"Created administrator '{username}'. The initial password is in {path} "
            "(change it after the first login and delete the file)"
        )
    else:
        logger.info(f"Created administrator '{username}' from OPENMANUS_ADMIN_PASSWORD")


async def active_admin_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_admin.is_(True), User.disabled.is_(False))
    )


async def run_ids_of(session: AsyncSession, conversation_ids: Iterable[str]) -> list:
    ids = list(conversation_ids)
    if not ids:
        return []
    return list(
        await session.scalars(select(Run.id).where(Run.conversation_id.in_(ids)))
    )


async def remove_files(settings: WebSettings, workspaces: list, run_ids: list) -> None:
    """Delete workspace directories and run artifacts (in a worker thread)."""

    def remove() -> None:
        for path in workspaces:
            files.remove_tree(path)
        for run_id in run_ids:
            files.remove_tree(settings.run_artifacts(run_id))

    await asyncio.to_thread(remove)


async def delete_conversations(
    session: AsyncSession, settings: WebSettings, conversations: list
) -> None:
    """Delete conversations with their messages, runs, events and files.

    Active runs must have been cancelled before. Commits the session.
    """
    if not conversations:
        return
    ids = [conversation.id for conversation in conversations]
    run_ids = await run_ids_of(session, ids)
    await session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
    await session.commit()
    await remove_files(
        settings,
        [settings.workspace_path(c.user_id, c.id) for c in conversations],
        run_ids,
    )
