"""Async database engine and session factory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import ColumnElement, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.web.models import SCHEMA_VERSION, AppSetting, Base


SQLITE_BUSY_TIMEOUT_MS = 15000
SCHEMA_VERSION_KEY = "schema_version"
CASEFOLD_FUNCTION = "om_casefold"


def _sqlite_file(url: str) -> Optional[Path]:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database)


def _secure_sqlite_file(path: Path) -> None:
    """Create the database file with mode 600 (SQLite copies it to -wal/-shm files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    else:
        os.chmod(path, 0o600)


def _casefold(value: Optional[str]) -> Optional[str]:
    return value.casefold() if isinstance(value, str) else value


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    # SQLite's LIKE/lower() only fold ASCII; searches use this Unicode-aware function.
    dbapi_connection.create_function(
        CASEFOLD_FUNCTION, 1, _casefold, deterministic=True
    )
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


class Database:
    """Owns the engine and hands out sessions (``async with db.session() as s``)."""

    def __init__(self, url: str):
        self.url = url
        self._sqlite_path = _sqlite_file(url)
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine.sync_engine, "connect", _configure_sqlite)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        """Create missing tables and record the schema version."""
        if self._sqlite_path is not None:
            _secure_sqlite_file(self._sqlite_path)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.session() as session:
            row = await session.get(AppSetting, SCHEMA_VERSION_KEY)
            if row is None:
                session.add(AppSetting(key=SCHEMA_VERSION_KEY, value=SCHEMA_VERSION))
                await session.commit()

    def contains_text(self, column: ColumnElement, needle: str) -> ColumnElement:
        """Case-insensitive (Unicode) substring match of ``needle`` in ``column``."""
        if self.engine.dialect.name == "sqlite":
            return getattr(func, CASEFOLD_FUNCTION)(column).contains(
                needle.casefold(), autoescape=True
            )
        return column.icontains(needle, autoescape=True)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.session() as session:
            value = await session.scalar(
                select(AppSetting.value).where(AppSetting.key == key)
            )
        return default if value is None else value

    async def set_setting(self, key: str, value: Any) -> None:
        async with self.session() as session:
            row = await session.get(AppSetting, key)
            if row is None:
                session.add(AppSetting(key=key, value=value))
            else:
                row.value = value
            await session.commit()
