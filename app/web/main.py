"""FastAPI application factory of the OpenManus web server.

``create_app()`` builds the app without touching the disk or the database; data
directories, the database schema, the admin bootstrap and run recovery happen in
the lifespan startup. ``app`` is created on first attribute access so that
``uvicorn app.web.main:app`` works while importing this module stays cheap.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.logger import logger
from app.web import files
from app.web.accounts import bootstrap_admin
from app.web.db import Database
from app.web.deps import Services
from app.web.middleware import (
    AccessLogMiddleware,
    RequestGuardMiddleware,
    SecurityHeadersMiddleware,
)
from app.web.routes import ROUTERS
from app.web.runs import RunManager, TaskRunner
from app.web.security import TokenService
from app.web.settings import WebSettings, app_version
from app.web.static import register_static


SHUTDOWN_TIMEOUT = 30.0
PRELOAD_WAIT = 15.0


def create_app(
    settings: Optional[WebSettings] = None, task_runner: Optional[TaskRunner] = None
) -> FastAPI:
    """Build the web application.

    Args:
        settings: Server settings (default: from ``OPENMANUS_*`` environment).
        task_runner: Replacement for ``app.flow.runner.run_task`` (tests).
    """
    settings = settings or WebSettings.from_env()
    db = Database(settings.database_url)
    services = Services(
        settings=settings, db=db, runs=RunManager(settings, db, task_runner)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Uploads interrupted by a crash leave staged files behind.
        files.remove_tree(settings.uploads_tmp_dir)
        settings.ensure_dirs()
        services.tokens = TokenService(
            settings.load_secret_key(), settings.session_days
        )
        await db.create_all()
        await bootstrap_admin(db, settings)
        await services.runs.recover()
        # Warm up the agent modules in the background (kept referenced until exit).
        preload = asyncio.create_task(services.runs.preload(), name="preload-core")
        logger.info(f"OpenManus web {app_version()} ready (data: {settings.data_dir})")
        try:
            yield
        finally:
            await services.runs.shutdown(SHUTDOWN_TIMEOUT)
            await db.dispose()
            # A worker thread cannot be cancelled; give a running import time to end.
            await asyncio.wait({preload}, timeout=PRELOAD_WAIT)

    app = FastAPI(
        title="OpenManus",
        version=app_version(),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.services = services

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, error: Exception):
        logger.exception(f"Unhandled error on {request.method} {request.url.path}")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    for router in ROUTERS:
        app.include_router(router, prefix="/api")
    register_static(app, settings.static_dir)

    # Added innermost first: guard → CORS → security headers → access log.
    app.add_middleware(
        RequestGuardMiddleware,
        trusted_origins=settings.cors_origins,
        trust_proxy=settings.trust_proxy,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    return app


def __getattr__(name: str):
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
