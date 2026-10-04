"""API routers, mounted under ``/api``."""

from app.web.routes import (
    agents,
    auth,
    conversations,
    files,
    runs,
    settings,
    system,
    users,
)


ROUTERS = (
    system.router,
    auth.router,
    agents.router,
    conversations.router,
    files.router,
    runs.router,
    settings.router,
    users.router,
)
