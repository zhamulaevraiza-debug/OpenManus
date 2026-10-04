"""Passwords, session tokens and login throttling."""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Optional, Tuple

import bcrypt
import jwt


SESSION_COOKIE = "om_session"
JWT_ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
BCRYPT_ROUNDS = 12
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.@-]{2,63}$")


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    """Normalized username, or ValueError with a user-facing message."""
    normalized = normalize_username(username)
    if not USERNAME_PATTERN.match(normalized):
        raise ValueError(
            "Username must be 3-64 characters: letters, digits, '_', '.', '@' or '-', "
            "starting with a letter or digit"
        )
    return normalized


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
    return password


@functools.cache
def _dummy_hash() -> str:
    """A valid hash, checked when the user does not exist so that response times do
    not reveal which usernames exist."""
    return bcrypt.hashpw(
        b"openmanus-dummy-password", bcrypt.gensalt(BCRYPT_ROUNDS)
    ).decode()


def _prehash(password: str) -> bytes:
    """SHA-256 + base64 so that passwords of any length fit bcrypt's 72-byte limit."""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def _hash_password_sync(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(BCRYPT_ROUNDS)).decode(
        "ascii"
    )


def _verify_password_sync(password: str, password_hash: Optional[str]) -> bool:
    if password_hash is None:
        bcrypt.checkpw(_prehash(password), _dummy_hash().encode("ascii"))
        return False
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except ValueError:
        return False


async def hash_password(password: str) -> str:
    """bcrypt hash (computed in a worker thread)."""
    return await asyncio.to_thread(_hash_password_sync, password)


async def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """Check a password; with ``password_hash=None`` a dummy check keeps timing equal."""
    return await asyncio.to_thread(_verify_password_sync, password, password_hash)


@dataclass(frozen=True)
class SessionClaims:
    user_id: str
    password_version: int


class TokenService:
    """Issues and verifies the HS256 session tokens stored in the session cookie."""

    def __init__(self, secret_key: str, session_days: int):
        self._secret_key = secret_key
        self.max_age = timedelta(days=session_days)

    def issue(self, user_id: str, password_version: int) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "pwd_v": password_version,
            "iat": now,
            "exp": now + self.max_age,
        }
        return jwt.encode(payload, self._secret_key, algorithm=JWT_ALGORITHM)

    def verify(self, token: str) -> Optional[SessionClaims]:
        """Claims of a valid, unexpired token; None otherwise."""
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[JWT_ALGORITHM],
                options={"require": ["sub", "exp", "pwd_v"]},
            )
        except jwt.PyJWTError:
            return None
        user_id, version = payload.get("sub"), payload.get("pwd_v")
        if not isinstance(user_id, str) or not isinstance(version, int):
            return None
        return SessionClaims(user_id, version)


class LoginThrottle:
    """Counts failed logins per (ip, username) in a sliding window."""

    def __init__(
        self, max_failures: int = 10, window_seconds: float = 600, max_keys: int = 10000
    ):
        self.max_failures = max_failures
        self.window = window_seconds
        self.max_keys = max_keys
        self._failures: "OrderedDict[Tuple[str, str], Deque[float]]" = OrderedDict()

    def _recent(self, key: Tuple[str, str], now: float) -> Deque[float]:
        attempts = self._failures.get(key)
        if attempts is None:
            return deque()
        while attempts and attempts[0] <= now - self.window:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
        return attempts

    def is_blocked(self, ip: str, username: str) -> bool:
        key = (ip, normalize_username(username))
        return len(self._recent(key, time.monotonic())) >= self.max_failures

    def record_failure(self, ip: str, username: str) -> None:
        key = (ip, normalize_username(username))
        now = time.monotonic()
        attempts = self._recent(key, now)
        attempts.append(now)
        self._failures[key] = attempts
        self._failures.move_to_end(key)
        while len(self._failures) > self.max_keys:
            self._failures.popitem(last=False)

    def reset(self, ip: str, username: str) -> None:
        self._failures.pop((ip, normalize_username(username)), None)
