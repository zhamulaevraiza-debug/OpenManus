"""Authentication: login, logout, registration, session info, password change."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import (
    Services,
    clear_session_cookie,
    client_ip,
    current_user,
    get_db,
    get_services,
    set_session_cookie,
)
from app.web.models import User
from app.web.schemas import ChangePassword, Credentials, user_out
from app.web.security import (
    hash_password,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
async def auth_config(services: Services = Depends(get_services)):
    return {"allow_registration": await services.allow_registration()}


@router.post("/login")
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    if services.throttle.is_blocked(ip, body.username):
        raise HTTPException(
            status_code=429, detail="Too many failed login attempts; try again later"
        )
    user = await session.scalar(
        select(User).where(User.username == normalize_username(body.username))
    )
    valid = await verify_password(
        body.password, user.password_hash if user is not None else None
    )
    if not valid:
        services.throttle.record_failure(ip, body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if user.disabled:
        raise HTTPException(status_code=403, detail="This account is disabled")
    services.throttle.reset(ip, body.username)
    set_session_cookie(response, request, services, user)
    return user_out(user)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, services: Services = Depends(get_services)
):
    clear_session_cookie(response, request, services)


@router.post("/register")
async def register(
    body: Credentials,
    request: Request,
    response: Response,
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    if not await services.allow_registration():
        raise HTTPException(status_code=403, detail="Registration is disabled")
    try:
        username = validate_username(body.username)
        validate_password(body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    exists = await session.scalar(
        select(func.count()).select_from(User).where(User.username == username)
    )
    if exists:
        raise HTTPException(status_code=409, detail="This username is already taken")
    user = User(username=username, password_hash=await hash_password(body.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="This username is already taken"
        ) from None
    set_session_cookie(response, request, services, user)
    return user_out(user)


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_out(user)


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePassword,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    try:
        validate_password(body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    stored = await session.get(User, user.id)
    if not await verify_password(body.current_password, stored.password_hash):
        raise HTTPException(status_code=400, detail="The current password is wrong")
    stored.password_hash = await hash_password(body.new_password)
    stored.password_version += 1
    await session.commit()
    if stored.username == services.settings.admin_username:
        services.settings.initial_password_file.unlink(missing_ok=True)
    set_session_cookie(response, request, services, stored)
