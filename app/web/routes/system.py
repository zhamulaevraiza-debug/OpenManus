"""Health and status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import config
from app.web.deps import Services, current_user, get_services
from app.web.models import User
from app.web.settings import app_version


router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    return {"status": "ok", "version": app_version()}


@router.get("/status")
async def status(
    user: User = Depends(current_user), services: Services = Depends(get_services)
):
    return {
        "version": app_version(),
        "llm_configured": config.llm_configured(),
        "active_runs": services.runs.active_count,
        "max_concurrent_runs": services.settings.max_concurrent_runs,
        "is_admin": bool(user.is_admin),
    }
