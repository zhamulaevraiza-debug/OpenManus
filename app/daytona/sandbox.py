"""Daytona sandbox lifecycle helpers.

The Daytona SDK is synchronous; every call is run in a worker thread so sandbox
provisioning never blocks the event loop. The client is created lazily (importing this
module needs neither the SDK nor an API key).
"""

import asyncio
import secrets
import threading
from typing import TYPE_CHECKING, Any, Optional, Tuple

from app.config import config
from app.context import emit
from app.utils.logger import logger


if TYPE_CHECKING:  # pragma: no cover - typing only
    from daytona import Daytona, Sandbox


SUPERVISORD_SESSION_ID = "supervisord-session"
SUPERVISORD_COMMAND = (
    "exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf"
)
# Services started by supervisord in the sandbox image.
AUTOMATION_API_PORT = 8003
VNC_PORT = 6080
WEBSITE_PORT = 8080
SERVICES_READY_TIMEOUT_SECONDS = 90
SERVICES_POLL_INTERVAL_SECONDS = 2

_client: Optional["Daytona"] = None
_client_lock = threading.Lock()


def daytona_configured() -> bool:
    """Whether a Daytona API key is configured."""
    settings = getattr(config, "daytona", None)
    return bool(settings and settings.daytona_api_key)


def get_daytona() -> "Daytona":
    """Return the shared Daytona client, creating it on first use.

    Raises:
        RuntimeError: when no Daytona API key is configured.
    """
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            if not daytona_configured():
                raise RuntimeError("Daytona API key not configured")
            from daytona import Daytona, DaytonaConfig

            settings = config.daytona
            _client = Daytona(
                DaytonaConfig(
                    api_key=settings.daytona_api_key,
                    server_url=settings.daytona_server_url,
                    target=settings.daytona_target,
                )
            )
            logger.info("Daytona client initialized")
    return _client


def generate_vnc_password() -> str:
    """A random password for the noVNC service of one sandbox."""
    return secrets.token_urlsafe(12)


async def _exec(sandbox: "Sandbox", command: str, timeout: int = 15) -> Any:
    return await asyncio.to_thread(sandbox.process.exec, command, timeout=timeout)


async def wait_for_services(
    sandbox: "Sandbox", timeout: float = SERVICES_READY_TIMEOUT_SECONDS
) -> None:
    """Poll until the browser automation API inside the sandbox answers."""
    probe = (
        "curl -s -o /dev/null -w '%{http_code}' "
        f"http://localhost:{AUTOMATION_API_PORT}/ || true"
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            response = await _exec(sandbox, probe)
            status = (getattr(response, "result", "") or "").strip()
            if status and status != "000":
                return
        except Exception as e:
            logger.debug(f"Sandbox readiness probe failed: {e}")
        if loop.time() >= deadline:
            logger.warning(
                f"Sandbox services not ready after {timeout:.0f}s; continuing anyway"
            )
            return
        await asyncio.sleep(SERVICES_POLL_INTERVAL_SECONDS)


async def start_supervisord_session(sandbox: "Sandbox") -> None:
    """Start supervisord (VNC, browser, automation API) and wait until it serves."""
    from daytona import SessionExecuteRequest

    try:
        logger.info(f"Creating session {SUPERVISORD_SESSION_ID} for supervisord")
        await asyncio.to_thread(sandbox.process.create_session, SUPERVISORD_SESSION_ID)
        await asyncio.to_thread(
            sandbox.process.execute_session_command,
            SUPERVISORD_SESSION_ID,
            SessionExecuteRequest(command=SUPERVISORD_COMMAND, run_async=True),
        )
    except Exception as e:
        logger.error(f"Error starting supervisord session: {str(e)}")
        raise
    await wait_for_services(sandbox)
    logger.info(f"Supervisord started in session {SUPERVISORD_SESSION_ID}")


async def get_sandbox_links(sandbox: "Sandbox") -> Tuple[str, str]:
    """Public preview URLs ``(vnc_url, website_url)`` of a sandbox."""

    def _links() -> Tuple[str, str]:
        vnc = sandbox.get_preview_link(VNC_PORT)
        website = sandbox.get_preview_link(WEBSITE_PORT)
        return (
            vnc.url if hasattr(vnc, "url") else str(vnc),
            website.url if hasattr(website, "url") else str(website),
        )

    return await asyncio.to_thread(_links)


async def announce_sandbox(
    sandbox: "Sandbox", vnc_password: Optional[str] = None
) -> Tuple[str, str]:
    """Emit ``sandbox.ready`` with the sandbox's preview URLs and return them.

    The URLs (and the VNC password) only travel over the run's event stream, which is
    visible to the owner of the run; they are not logged.
    """
    vnc_url, website_url = await get_sandbox_links(sandbox)
    data = {"vnc_url": vnc_url, "website_url": website_url}
    if vnc_password:
        data["vnc_password"] = vnc_password
    emit("sandbox.ready", **data)
    logger.info(f"Sandbox {sandbox.id} is ready")
    return vnc_url, website_url


async def get_or_start_sandbox(sandbox_id: str) -> "Sandbox":
    """Retrieve a sandbox by ID, check its state, and start it if needed."""
    from daytona import SandboxState

    logger.info(f"Getting or starting sandbox with ID: {sandbox_id}")
    client = get_daytona()
    try:
        sandbox = await asyncio.to_thread(client.get, sandbox_id)
        if sandbox.state in (SandboxState.ARCHIVED, SandboxState.STOPPED):
            logger.info(f"Sandbox is in {sandbox.state} state. Starting...")
            await asyncio.to_thread(client.start, sandbox)
            sandbox = await asyncio.to_thread(client.get, sandbox_id)
            await start_supervisord_session(sandbox)
            await announce_sandbox(sandbox)
        logger.info(f"Sandbox {sandbox_id} is ready")
        return sandbox
    except Exception as e:
        logger.error(f"Error retrieving or starting sandbox: {str(e)}")
        raise


def _sandbox_params(password: str, project_id: Optional[str]) -> Any:
    from daytona import CreateSandboxFromImageParams, Resources

    labels = {"id": project_id} if project_id else None
    return CreateSandboxFromImageParams(
        image=config.daytona.sandbox_image_name,
        public=True,
        labels=labels,
        env_vars={
            "CHROME_PERSISTENT_SESSION": "true",
            "RESOLUTION": "1024x768x24",
            "RESOLUTION_WIDTH": "1024",
            "RESOLUTION_HEIGHT": "768",
            "VNC_PASSWORD": password,
            "ANONYMIZED_TELEMETRY": "false",
            "CHROME_PATH": "",
            "CHROME_USER_DATA": "",
            "CHROME_DEBUGGING_PORT": "9222",
            "CHROME_DEBUGGING_HOST": "localhost",
            "CHROME_CDP": "",
        },
        resources=Resources(
            cpu=2,
            memory=4,
            disk=5,
        ),
        auto_stop_interval=15,
        auto_archive_interval=24 * 60,
    )


async def _provision(password: str, project_id: Optional[str]) -> "Sandbox":
    """Create a sandbox and wait for its services; deleted again on failure."""
    logger.info("Creating new Daytona sandbox environment")
    client = get_daytona()
    sandbox = await asyncio.to_thread(
        client.create, _sandbox_params(password, project_id)
    )
    logger.info(f"Sandbox created with ID: {sandbox.id}")
    try:
        await start_supervisord_session(sandbox)
    except BaseException:
        await _delete_quietly(client, sandbox)
        raise
    return sandbox


async def provision_sandbox(
    password: Optional[str] = None, project_id: Optional[str] = None
) -> "Sandbox":
    """Create a sandbox with all services running and emit ``sandbox.ready``.

    A random VNC password is generated unless ``password`` is given; it is included
    in the event so the owner of the run can open the VNC view.
    """
    password = password or generate_vnc_password()
    sandbox = await _provision(password, project_id)
    await announce_sandbox(sandbox, vnc_password=password)
    return sandbox


def create_sandbox(
    password: Optional[str] = None, project_id: Optional[str] = None
) -> "Sandbox":
    """Blocking variant of :func:`provision_sandbox` for worker threads.

    Meant for ``await asyncio.to_thread(create_sandbox, ...)``; it runs its own event
    loop and therefore fails fast if called on an event-loop thread. It does not
    emit events (the caller announces the sandbox).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_provision(password or generate_vnc_password(), project_id))
    raise RuntimeError(
        "create_sandbox() blocks: run it with asyncio.to_thread() or await "
        "provision_sandbox() instead"
    )


async def _delete_quietly(client: "Daytona", sandbox: "Sandbox") -> None:
    try:
        await asyncio.to_thread(client.delete, sandbox)
    except Exception as e:
        logger.warning(f"Could not delete sandbox {sandbox.id}: {e}")


async def delete_sandbox(sandbox_id: str) -> bool:
    """Delete a sandbox by its ID."""
    logger.info(f"Deleting sandbox with ID: {sandbox_id}")
    client = get_daytona()
    try:
        sandbox = await asyncio.to_thread(client.get, sandbox_id)
        await asyncio.to_thread(client.delete, sandbox)
        logger.info(f"Successfully deleted sandbox {sandbox_id}")
        return True
    except Exception as e:
        logger.error(f"Error deleting sandbox {sandbox_id}: {str(e)}")
        raise
