"""Shared services and FastAPI dependencies (authentication, ownership checks)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.db import Database
from app.web.models import Conversation, Run, User
from app.web.runs import RunManager
from app.web.security import SESSION_COOKIE, LoginThrottle, TokenService
from app.web.settings import WebSettings


ALLOW_REGISTRATION_KEY = "allow_registration"


@dataclass
class Services:
    """Process-wide objects shared by all requests (``app.state.services``)."""

    settings: WebSettings
    db: Database
    runs: RunManager
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    tokens: Optional[TokenService] = None  # available once the app has started
    settings_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def allow_registration(self) -> bool:
        value = await self.db.get_setting(
            ALLOW_REGISTRATION_KEY, self.settings.allow_registration
        )
        return bool(value)


def get_services(request: Request) -> Services:
    return request.app.state.services


async def get_db(
    services: Services = Depends(get_services),
) -> AsyncIterator[AsyncSession]:
    async with services.db.session() as session:
        yield session


def client_ip(request: Request) -> str:
    """Client address (uvicorn applies trusted X-Forwarded-For to the scope)."""
    return request.client.host if request.client else "unknown"


def _is_secure_request(request: Request, settings: WebSettings) -> bool:
    if settings.cookie_secure != "auto":
        return settings.cookie_secure == "true"
    if request.url.scheme == "https":
        return True
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-proto", "")
        return forwarded.split(",")[0].strip().lower() == "https"
    return False


def set_session_cookie(
    response: Response, request: Request, services: Services, user: User
) -> None:
    token = services.tokens.issue(user.id, user.password_version)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(services.tokens.max_age.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request, services.settings),
        path="/",
    )


def clear_session_cookie(response: Response, request: Request, services: Services):
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request, services.settings),
        path="/",
    )


_UNAUTHENTICATED = HTTPException(status_code=401, detail="Not authenticated")


async def current_user(
    request: Request, services: Services = Depends(get_services)
) -> User:
    """The signed-in user; 401 for missing/invalid/revoked sessions or disabled users.

    Uses its own short-lived session so that long responses (event streams) do not
    hold a database connection.
    """
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    token = request.cookies.get(SESSION_COOKIE)
    claims = services.tokens.verify(token) if token else None
    if claims is None:
        raise _UNAUTHENTICATED
    async with services.db.session() as session:
        user = await session.get(User, claims.user_id)
    if (
        user is None
        or user.disabled
        or user.password_version != claims.password_version
    ):
        raise _UNAUTHENTICATED
    request.state.user = user
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


async def load_conversation(
    session: AsyncSession, conversation_id: str, user: User
) -> Conversation:
    """The user's conversation or 404 (also for other users' conversations)."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


async def load_run(session: AsyncSession, run_id: str, user: User) -> Run:
    """The user's run or 404."""
    run = await session.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
