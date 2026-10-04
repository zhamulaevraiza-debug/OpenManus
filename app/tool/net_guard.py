"""SSRF protection for tools that fetch or browse URLs chosen by the model.

Only ``http``/``https`` URLs whose host resolves exclusively to public addresses are
allowed. Loopback, private, link-local (incl. cloud metadata endpoints), CGNAT,
multicast, reserved and unspecified ranges are blocked, including their IPv6
spellings (IPv4-mapped, 6to4, Teredo, NAT64). Setting the environment variable
``OPENMANUS_ALLOW_PRIVATE_NETWORK=true`` lifts the address restriction (the scheme
restriction always applies).

The browser integration (:func:`guard_browser_context`) blocks every request the page
makes to a forbidden address. Playwright does not expose redirect hops to route
handlers, so redirects are detected from request events and pages that ended up on a
forbidden address are reset by :func:`enforce_browser_policy`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Any, Iterable, List, Optional, Set, Union
from urllib.parse import urlsplit

from app.exceptions import ToolError
from app.logger import logger


ALLOW_PRIVATE_NETWORK_ENV = "OPENMANUS_ALLOW_PRIVATE_NETWORK"
ALLOWED_SCHEMES = ("http", "https")

_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
_LOCAL_HOSTNAMES = ("localhost", "localhost.localdomain", "ip6-localhost")
_BROWSER_INTERNAL_SCHEMES = ("about", "data", "blob", "chrome-error")
_GUARD_STATE_ATTR = "_openmanus_ssrf_guard"

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


class UnsafeURLError(ToolError):
    """Raised when a URL targets a forbidden scheme or network address."""


def private_network_allowed() -> bool:
    """Whether the operator explicitly allowed access to private networks."""
    value = os.environ.get(ALLOW_PRIVATE_NETWORK_ENV, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_ip(address: Union[str, IPAddress]) -> bool:
    """True when ``address`` is a globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            return False
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            return is_public_ip(embedded)
    return ip.is_global and not ip.is_multicast


def _is_local_hostname(host: str) -> bool:
    host = host.rstrip(".").lower()
    return host in _LOCAL_HOSTNAMES or host.endswith(".localhost")


def _parse(url: str):
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as e:
        raise UnsafeURLError(f"Invalid URL '{url}': {e}") from None
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Blocked URL '{url}': only http and https URLs are allowed"
        )
    host = parts.hostname
    if not host:
        raise UnsafeURLError(f"Invalid URL '{url}': missing host")
    return host, port or (443 if scheme == "https" else 80)


def _check_addresses(url: str, host: str, addresses: Iterable[str]) -> None:
    blocked = sorted({a for a in addresses if not is_public_ip(a)})
    if blocked:
        raise UnsafeURLError(
            f"Blocked URL '{url}': host '{host}' resolves to a private or reserved "
            f"address ({', '.join(blocked)})"
        )


def _literal_address(host: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


async def _resolve(host: str, port: int) -> List[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _resolve_sync(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _precheck(url: str) -> Optional[tuple]:
    """Validate scheme and literal hosts; return ``(host, port)`` if DNS is needed."""
    host, port = _parse(url)
    if private_network_allowed():
        return None
    if _is_local_hostname(host):
        raise UnsafeURLError(f"Blocked URL '{url}': local host names are not allowed")
    literal = _literal_address(host)
    if literal is not None:
        _check_addresses(url, host, [literal])
        return None
    return host, port


async def check_url(url: str) -> str:
    """Validate ``url`` for fetching/browsing; returns it unchanged when allowed.

    Raises :class:`UnsafeURLError` for non-http(s) schemes or hosts that resolve to a
    non-public address. Unresolvable hosts are allowed (the fetch itself will fail).
    """
    pending = _precheck(url)
    if pending is None:
        return url
    host, port = pending
    try:
        addresses = await _resolve(host, port)
    except (socket.gaierror, UnicodeError) as e:
        logger.debug(f"Could not resolve {host} while checking {url}: {e}")
        return url
    _check_addresses(url, host, addresses)
    return url


def check_url_sync(url: str) -> str:
    """Blocking variant of :func:`check_url` for code running in worker threads."""
    pending = _precheck(url)
    if pending is None:
        return url
    host, port = pending
    try:
        addresses = _resolve_sync(host, port)
    except (socket.gaierror, UnicodeError) as e:
        logger.debug(f"Could not resolve {host} while checking {url}: {e}")
        return url
    _check_addresses(url, host, addresses)
    return url


async def is_url_allowed(url: str) -> bool:
    """Non-raising convenience wrapper around :func:`check_url`."""
    try:
        await check_url(url)
        return True
    except UnsafeURLError:
        return False


def _is_browser_internal(url: str) -> bool:
    return url.split(":", 1)[0].lower() in _BROWSER_INTERNAL_SCHEMES


class _BrowserGuardState:
    """Redirect hops to forbidden addresses observed on one browser context."""

    def __init__(self) -> None:
        self.blocked: List[str] = []
        self.pending: Set[asyncio.Task] = set()

    def on_request(self, request: Any) -> None:
        if request.redirected_from is None or not request.is_navigation_request():
            return
        task = asyncio.ensure_future(self._check(request.url))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _check(self, url: str) -> None:
        if not await is_url_allowed(url):
            logger.warning(f"Browser redirect to a blocked address: {url}")
            self.blocked.append(url)

    async def drain(self) -> List[str]:
        if self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)
        blocked, self.blocked = self.blocked, []
        return blocked


async def _route_guard(route: Any, request: Any) -> None:
    url = request.url
    lowered = url.lower()
    if lowered.startswith("about:blank") or (
        _is_browser_internal(url) and not request.is_navigation_request()
    ):
        await route.continue_()
        return
    try:
        await check_url(url)
    except UnsafeURLError as e:
        logger.warning(f"Browser request blocked: {e.message}")
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def guard_browser_context(context: Any) -> None:
    """Install the SSRF guard on a Playwright ``BrowserContext``.

    Every request (navigations, frames, sub-resources, popups) to a forbidden scheme
    or address is aborted. Redirect hops are not routed by Playwright, so they are
    recorded and handled by :func:`enforce_browser_policy`. Idempotent per context.
    """
    if getattr(context, _GUARD_STATE_ATTR, None) is not None:
        return
    state = _BrowserGuardState()
    await context.route("**/*", _route_guard)
    context.on("request", state.on_request)
    setattr(context, _GUARD_STATE_ATTR, state)


async def enforce_browser_policy(context: Any) -> List[str]:
    """Reset pages/frames that reached a forbidden address; return their URLs.

    Call after every browser action (and before reading page content) so content
    obtained through a redirect to a private address never reaches the model.
    """
    violations: List[str] = []
    state: Optional[_BrowserGuardState] = getattr(context, _GUARD_STATE_ATTR, None)
    if state is not None:
        violations.extend(await state.drain())
    for page in list(context.pages):
        for frame in list(page.frames):
            url = frame.url
            if not url or _is_browser_internal(url):
                continue
            if await is_url_allowed(url):
                continue
            violations.append(url)
            try:
                await frame.goto("about:blank")
            except Exception as e:  # the frame may have been detached meanwhile
                logger.debug(f"Could not reset frame at {url}: {e}")
    return list(dict.fromkeys(violations))
