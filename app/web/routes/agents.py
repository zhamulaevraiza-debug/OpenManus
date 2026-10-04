"""Agents and run modes available to the signed-in user."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.web.deps import current_user
from app.web.modes import BUILTIN_MODES, agent_infos


router = APIRouter(tags=["agents"], dependencies=[Depends(current_user)])


@router.get("/agents")
async def agents():
    infos = await asyncio.to_thread(agent_infos)
    return {"modes": [{"key": key} for key in BUILTIN_MODES], "agents": infos}
