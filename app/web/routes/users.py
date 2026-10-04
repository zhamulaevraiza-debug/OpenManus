"""Admin: user management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.accounts import active_admin_count, remove_files, run_ids_of
from app.web.deps import Services, admin_user, get_db, get_services
from app.web.models import Conversation, User
from app.web.schemas import UserCreate, UserUpdate, user_out
from app.web.security import hash_password, validate_password, validate_username


router = APIRouter(prefix="/users", tags=["users"])


async def _load_user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@router.get("")
async def list_users(
    admin: User = Depends(admin_user), session: AsyncSession = Depends(get_db)
):
    users = await session.scalars(select(User).order_by(User.created_at))
    return [user_out(user) for user in users]


@router.post("")
async def create_user(
    body: UserCreate,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        username = validate_username(body.username)
        validate_password(body.password)
    except ValueError as e:
        raise _bad_request(e) from None
    taken = await session.scalar(
        select(func.count()).select_from(User).where(User.username == username)
    )
    if taken:
        raise HTTPException(status_code=409, detail="This username is already taken")
    user = User(
        username=username,
        password_hash=await hash_password(body.password),
        is_admin=body.is_admin,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="This username is already taken"
        ) from None
    return user_out(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db),
):
    user = await _load_user(session, user_id)
    removes_admin = (
        user.is_admin
        and not user.disabled
        and (body.is_admin is False or body.disabled is True)
    )
    if user.id == admin.id and (body.is_admin is False or body.disabled is True):
        raise HTTPException(
            status_code=400, detail="You cannot demote or disable your own account"
        )
    if removes_admin and await active_admin_count(session) <= 1:
        raise HTTPException(
            status_code=400, detail="At least one active administrator is required"
        )
    if body.password is not None:
        try:
            validate_password(body.password)
        except ValueError as e:
            raise _bad_request(e) from None
        user.password_hash = await hash_password(body.password)
        user.password_version += 1
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.disabled is not None:
        if body.disabled and not user.disabled:
            user.password_version += 1  # end existing sessions
        user.disabled = body.disabled
    await session.commit()
    return user_out(user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    admin: User = Depends(admin_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    user = await _load_user(session, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=400, detail="You cannot delete your own account"
        )
    if user.is_admin and not user.disabled and await active_admin_count(session) <= 1:
        raise HTTPException(
            status_code=400, detail="At least one active administrator is required"
        )
    await services.runs.cancel_where(user_id=user.id)
    conversation_ids = list(
        await session.scalars(
            select(Conversation.id).where(Conversation.user_id == user.id)
        )
    )
    run_ids = await run_ids_of(session, conversation_ids)
    await session.delete(user)
    await session.commit()
    await remove_files(
        services.settings, [services.settings.user_workspaces(user.id)], run_ids
    )
