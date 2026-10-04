"""Admin: application settings and LLM connection test."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.web.config_service import (
    SettingsError,
    SettingsUpdate,
    apply_update,
    settings_view,
    test_llm,
)
from app.web.deps import ALLOW_REGISTRATION_KEY, Services, admin_user, get_services
from app.web.schemas import TestLLM


router = APIRouter(
    prefix="/settings", tags=["settings"], dependencies=[Depends(admin_user)]
)


async def _view(services: Services) -> dict:
    allow_registration = await services.allow_registration()
    return await asyncio.to_thread(settings_view, allow_registration)


@router.get("")
async def get_settings(services: Services = Depends(get_services)):
    return await _view(services)


@router.put("")
async def update_settings(
    body: SettingsUpdate, services: Services = Depends(get_services)
):
    async with services.settings_lock:
        try:
            await asyncio.to_thread(apply_update, body)
        except SettingsError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        if body.allow_registration is not None:
            await services.db.set_setting(
                ALLOW_REGISTRATION_KEY, body.allow_registration
            )
    return await _view(services)


@router.post("/test-llm")
async def test_llm_connection(body: TestLLM):
    return await test_llm(body.which)
