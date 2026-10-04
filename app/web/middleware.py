"""ASGI middleware: request guard (CSRF/body limits), security headers, access log.

Implemented as plain ASGI middleware so that streaming responses (SSE, downloads)
pass through unbuffered and client disconnects propagate.
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logger import logger


MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALLOWED_BODY_TYPES = ("application/json", "multipart/form-data")
MAX_JSON_BODY = 1024 * 1024

APP_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "frame-src 'self' blob:; worker-src 'self'; manifest-src 'self'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
SECURITY_HEADERS: Tuple[Tuple[bytes, bytes], ...] = (
    (b"content-security-policy", APP_CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(self), microphone=(self), geolocation=()"),
    (b"x-frame-options", b"SAMEORIGIN"),
)
FILE_RESPONSE_HEADERS: Tuple[Tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
)
# Workspace files, artifacts and downloads carry their own policy (see routes.files).
FILE_PATH = re.compile(
    r"^/api/(conversations/[^/]+/(files/.+|files\.zip|export\.md)"
    r"|runs/[^/]+/artifacts/[^/]+)$"
)


async def _send_json(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestGuardMiddleware:
    """Protects mutating API requests.

    * ``Origin`` (when present) must match the request host or a configured CORS
      origin, otherwise 403 (cross-site request forgery defence).
    * A request body must be JSON or multipart (415 otherwise).
    * JSON bodies are limited to 1 MB (413).
    """

    def __init__(
        self, app: ASGIApp, trusted_origins: Iterable[str] = (), trust_proxy=False
    ):
        self.app = app
        self.trusted_origins = {origin.lower() for origin in trusted_origins}
        self.trust_proxy = trust_proxy

    def _origin_allowed(self, origin: str, headers: Headers) -> bool:
        origin = origin.strip().lower().rstrip("/")
        if origin in self.trusted_origins:
            return True
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        host = headers.get("host", "")
        if self.trust_proxy and headers.get("x-forwarded-host"):
            host = headers["x-forwarded-host"].split(",")[0]
        return parts.netloc == host.strip().lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in MUTATING_METHODS
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and not self._origin_allowed(origin, headers):
            await _send_json(send, 403, "Cross-origin request rejected")
            return

        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        length = headers.get("content-length")
        has_body = bool(
            (length and length.strip() != "0") or headers.get("transfer-encoding")
        )
        if (has_body or content_type) and content_type not in ALLOWED_BODY_TYPES:
            await _send_json(send, 415, "Request body must be JSON or multipart")
            return
        if content_type != "application/json":
            await self.app(scope, receive, send)
            return
        if length and length.isdigit() and int(length) > MAX_JSON_BODY:
            await _send_json(send, 413, "Request body is too large")
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_JSON_BODY:
                    # Raised while the endpoint reads its body; the app's exception
                    # middleware turns it into a JSON 413 response.
                    raise HTTPException(413, "Request body is too large")
            return message

        await self.app(scope, limited_receive, send)


class SecurityHeadersMiddleware:
    """Adds security headers (without overriding ones set by the response)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = (
            FILE_RESPONSE_HEADERS
            if FILE_PATH.match(scope["path"])
            else SECURITY_HEADERS
        )

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: List[Tuple[bytes, bytes]] = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(item for item in extra if item[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class AccessLogMiddleware:
    """One ``key=value`` log line per request: method, path (never the query string,
    cookies or headers), status, duration and client address."""

    QUIET_PATHS = ("/api/health",)

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status: Optional[int] = None

        async def recording_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, recording_send)
        finally:
            path = scope["path"]
            client = scope.get("client")
            line = (
                f"http method={scope['method']} path={path} "
                f"status={status if status is not None else '-'} "
                f"duration_ms={(time.perf_counter() - started) * 1000:.0f} "
                f"client={client[0] if client else '-'}"
            )
            if path.startswith("/api/") and path not in self.QUIET_PATHS:
                logger.info(line)
            else:
                logger.debug(line)
