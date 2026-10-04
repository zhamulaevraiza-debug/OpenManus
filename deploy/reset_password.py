"""Reset the password of an OpenManus web account, e.g. a forgotten admin password.

Run it where the server runs, with the same ``OPENMANUS_*`` environment::

    docker compose exec openmanus python deploy/reset_password.py admin
    docker compose exec openmanus python deploy/reset_password.py admin --generate

The new password is asked for twice, or generated and printed with ``--generate``.
The account is re-enabled and all of its sessions are signed out. It is safe to run
while the server is running.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
from pathlib import Path
from typing import List, Optional


# The repository root holds the ``app`` package; the script may be run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.web.db import Database  # noqa: E402
from app.web.models import User  # noqa: E402
from app.web.security import (  # noqa: E402
    hash_password,
    normalize_username,
    validate_password,
)
from app.web.settings import WebSettings  # noqa: E402


GENERATED_PASSWORD_BYTES = 12


class ResetError(Exception):
    """A user-facing reason why the password could not be reset."""


def _check_database_exists(database_url: str) -> None:
    """Refuse to create an empty SQLite file when the path is wrong."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return
    if url.database != ":memory:" and not Path(url.database).exists():
        raise ResetError(
            f"Database {url.database} not found; check OPENMANUS_DATA_DIR "
            "or OPENMANUS_DATABASE_URL"
        )


async def reset_password(settings: WebSettings, username: str, password: str) -> str:
    """Set a new password for ``username``, re-enable it and revoke its sessions.

    Returns:
        The normalized username.

    Raises:
        ResetError: When the password is invalid, or the database or user is missing.
    """
    try:
        validate_password(password)
    except ValueError as e:
        raise ResetError(str(e)) from None
    _check_database_exists(settings.database_url)
    name = normalize_username(username)
    db = Database(settings.database_url)
    try:
        async with db.session() as session:
            user = await session.scalar(select(User).where(User.username == name))
            if user is None:
                raise ResetError(f"There is no user named '{name}'")
            user.password_hash = await hash_password(password)
            user.password_version = (user.password_version or 0) + 1
            user.disabled = False
            await session.commit()
    finally:
        await db.dispose()
    return name


def _ask_password() -> str:
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat the new password: "):
        raise ResetError("The passwords do not match")
    return password


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset the password of an OpenManus web account."
    )
    parser.add_argument("username", help="account to reset, e.g. admin")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="generate a random password and print it instead of asking for one",
    )
    args = parser.parse_args(argv)

    try:
        settings = WebSettings.from_env()
        if args.generate:
            password = secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)
        else:
            password = _ask_password()
        name = asyncio.run(reset_password(settings, args.username, password))
    except (ResetError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"The password of '{name}' was reset; its sessions are signed out.")
    if args.generate:
        print(f"New password: {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
