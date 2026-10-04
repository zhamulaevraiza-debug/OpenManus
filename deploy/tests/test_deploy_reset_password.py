"""deploy/reset_password.py: offline password reset of web accounts."""

import asyncio

import pytest
import reset_password
from sqlalchemy import select

from app.web.db import Database
from app.web.models import User
from app.web.security import hash_password, verify_password
from app.web.settings import WebSettings


OLD_PASSWORD = "old-password-1"


@pytest.fixture
def settings(tmp_path, monkeypatch) -> WebSettings:
    monkeypatch.setenv("OPENMANUS_DATA_DIR", str(tmp_path / "data"))
    for name in ("OPENMANUS_DATABASE_URL", "OPENMANUS_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    return WebSettings.from_env()


async def _create_user(settings: WebSettings, username: str, disabled=False) -> None:
    settings.ensure_dirs()
    db = Database(settings.database_url)
    try:
        await db.create_all()
        async with db.session() as session:
            session.add(
                User(
                    username=username,
                    password_hash=await hash_password(OLD_PASSWORD),
                    is_admin=True,
                    disabled=disabled,
                )
            )
            await session.commit()
    finally:
        await db.dispose()


async def _load_user(settings: WebSettings, username: str) -> User:
    db = Database(settings.database_url)
    try:
        async with db.session() as session:
            return await session.scalar(select(User).where(User.username == username))
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_reset_sets_password_revokes_sessions_and_enables(settings):
    await _create_user(settings, "admin", disabled=True)

    name = await reset_password.reset_password(settings, " Admin ", "brand-new-pass")

    assert name == "admin"
    user = await _load_user(settings, "admin")
    assert await verify_password("brand-new-pass", user.password_hash)
    assert not await verify_password(OLD_PASSWORD, user.password_hash)
    assert user.password_version == 2
    assert user.disabled is False


@pytest.mark.asyncio
async def test_short_password_is_rejected_without_changes(settings):
    await _create_user(settings, "admin")

    with pytest.raises(reset_password.ResetError, match="at least 8"):
        await reset_password.reset_password(settings, "admin", "short")

    user = await _load_user(settings, "admin")
    assert await verify_password(OLD_PASSWORD, user.password_hash)
    assert user.password_version == 1


@pytest.mark.asyncio
async def test_unknown_user_is_reported(settings):
    await _create_user(settings, "admin")

    with pytest.raises(reset_password.ResetError, match="no user named 'alice'"):
        await reset_password.reset_password(settings, "alice", "brand-new-pass")


@pytest.mark.asyncio
async def test_missing_database_is_not_created(settings):
    with pytest.raises(reset_password.ResetError, match="not found"):
        await reset_password.reset_password(settings, "admin", "brand-new-pass")

    assert not (settings.data_dir / "openmanus.db").exists()


def test_main_generate_prints_a_working_password(settings, capsys):
    asyncio.run(_create_user(settings, "admin"))

    assert reset_password.main(["admin", "--generate"]) == 0

    output = capsys.readouterr().out
    password = output.split("New password: ", 1)[1].strip()
    user = asyncio.run(_load_user(settings, "admin"))
    assert asyncio.run(verify_password(password, user.password_hash))


def test_main_rejects_mismatched_confirmation(settings, monkeypatch, capsys):
    asyncio.run(_create_user(settings, "admin"))
    answers = iter(["first-password", "second-password"])
    monkeypatch.setattr(reset_password.getpass, "getpass", lambda prompt: next(answers))

    assert reset_password.main(["admin"]) == 1
    assert "do not match" in capsys.readouterr().err
