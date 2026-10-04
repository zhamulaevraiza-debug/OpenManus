"""Start the OpenManus web server (REST/SSE API + web UI).

Configuration comes from ``OPENMANUS_*`` environment variables (see
``app/web/settings.py``). The server runs a single worker process because active
runs are managed in memory.
"""

import asyncio
import os
import sys


# Browsers must run headless on a server unless configured otherwise; this has to be
# set before the core configuration is imported.
os.environ.setdefault("OPENMANUS_BROWSER_HEADLESS", "1")

import uvicorn  # noqa: E402

from app.logger import logger  # noqa: E402
from app.web.main import create_app  # noqa: E402
from app.web.settings import WebSettings  # noqa: E402


GRACEFUL_SHUTDOWN_SECONDS = 10
# Proxies whose X-Forwarded-* headers are trusted (when OPENMANUS_TRUST_PROXY is on):
# loopback and private networks, i.e. a reverse proxy on the host or in Docker.
# Override with uvicorn's FORWARDED_ALLOW_IPS (comma list of addresses/networks).
DEFAULT_TRUSTED_PROXIES = (
    "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
)


class _Server(uvicorn.Server):
    """Ends live event streams as soon as shutdown begins, so that open SSE
    connections do not delay the graceful shutdown."""

    def __init__(self, config: uvicorn.Config, on_exit):
        super().__init__(config)
        self._on_exit = on_exit

    def handle_exit(self, sig, frame) -> None:
        super().handle_exit(sig, frame)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon_threadsafe(self._on_exit)


def main() -> int:
    try:
        settings = WebSettings.from_env()
    except ValueError as e:
        logger.error(f"Invalid configuration: {e}")
        return 2
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=settings.trust_proxy,
        forwarded_allow_ips=(
            os.environ.get("FORWARDED_ALLOW_IPS") or DEFAULT_TRUSTED_PROXIES
        ),
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        access_log=False,
        server_header=False,
        ws="none",  # live updates use Server-Sent Events
    )
    runs = app.state.services.runs
    server = _Server(config, on_exit=runs.begin_shutdown)
    logger.info(f"Starting OpenManus web on http://{settings.host}:{settings.port}")
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
