"""Runs: status, live events (SSE and polling), cancel, answers and artifacts."""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import Services, current_user, get_db, get_services, load_run
from app.web.events import (
    CLOSED,
    DROPPED,
    FINISHED_EVENT,
    Subscription,
    format_sse,
    load_events,
)
from app.web.models import User
from app.web.schemas import Answer, run_out


router = APIRouter(prefix="/runs", tags=["runs"])

PING_INTERVAL = 15.0
REPLAY_PAGE = 500
MAX_POLL_EVENTS = 500
CANCEL_WAIT = 3.0
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.(png|jpg)$")


def _start_seq(after: int, last_event_id: Optional[str]) -> int:
    """Resume point: the larger of ``after`` and a numeric ``Last-Event-ID``."""
    if last_event_id and last_event_id.strip().isdigit():
        return max(after, int(last_event_id.strip()))
    return after


async def _owned_run(services: Services, run_id: str, user: User):
    async with services.db.session() as session:
        return await load_run(session, run_id, user)


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    run = await load_run(session, run_id, user)
    return run_out(run, services.runs.live_seq(run.id))


async def _event_stream(
    services: Services, run_id: str, after: int
) -> AsyncIterator[str]:
    """Replay persisted events after ``after``, then follow the live run.

    The live subscription is taken before reading the database so that no event
    falls between the replay and the live part (duplicates are skipped by seq).
    """
    last = after
    subscription: Optional[Subscription] = services.runs.subscribe(run_id)
    try:
        yield ": connected\n\n"
        while True:
            events = await load_events(services.db, run_id, last, REPLAY_PAGE)
            for event in events:
                yield format_sse(event)
                last = event["seq"]
                if event["type"] == FINISHED_EVENT:
                    return
            if len(events) < REPLAY_PAGE:
                break
        if subscription is None:
            return
        for event in subscription.backlog:
            if event["seq"] > last:
                yield format_sse(event)
                last = event["seq"]
        while True:
            try:
                item = await asyncio.wait_for(
                    subscription.queue.get(), timeout=PING_INTERVAL
                )
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is CLOSED or item is DROPPED:
                return
            if item["seq"] <= last:
                continue
            yield format_sse(item)
            last = item["seq"]
            if item["type"] == FINISHED_EVENT:
                return
    finally:
        if subscription is not None:
            services.runs.unsubscribe(run_id, subscription)


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
):
    await _owned_run(services, run_id, user)
    start = _start_seq(after, request.headers.get("last-event-id"))
    return StreamingResponse(
        _event_stream(services, run_id, start),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/events.json")
async def run_events_json(
    run_id: str,
    after: int = Query(0, ge=0),
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
):
    await _owned_run(services, run_id, user)
    events = await load_events(services.db, run_id, after, MAX_POLL_EVENTS)
    if len(events) < MAX_POLL_EVENTS:
        last = events[-1]["seq"] if events else after
        events += services.runs.unpersisted_events(run_id, last)
    return events[:MAX_POLL_EVENTS]


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
):
    await _owned_run(services, run_id, user)
    await services.runs.cancel(run_id, wait=CANCEL_WAIT)
    run = await _owned_run(services, run_id, user)
    return run_out(run, services.runs.live_seq(run.id))


@router.post("/{run_id}/answer", status_code=204)
async def answer_question(
    run_id: str,
    body: Answer,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
):
    await _owned_run(services, run_id, user)
    if not services.runs.answer(run_id, body.question_id, body.answer):
        raise HTTPException(status_code=404, detail="No such pending question")


@router.get("/{run_id}/artifacts/{name}")
async def run_artifact(
    run_id: str,
    name: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
):
    await _owned_run(services, run_id, user)
    if not ARTIFACT_NAME.match(name):
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = services.settings.run_artifacts(run_id) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(
        path,
        media_type="image/png" if name.endswith(".png") else "image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
