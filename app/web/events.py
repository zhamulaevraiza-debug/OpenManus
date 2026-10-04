"""Per-run event pipeline: sequencing, artifacts, persistence and live broadcast.

The core calls :meth:`RunEventPipeline.emit` (the run's ``EventSink``) synchronously
from the event loop. Events are queued and handled in order by a consumer task which

* assigns the per-run ``seq`` and timestamp,
* stores ``image_b64`` screenshots as artifact files (the event then carries an
  ``image_url`` instead),
* emits ``workspace.changed`` after tool results that changed workspace files,
* broadcasts to live subscribers (bounded queues; a slow subscriber is dropped and
  resumes from the database with ``after=<seq>``),
* persists events in small batches (at most every ``FLUSH_INTERVAL`` seconds, at
  once for important events), up to ``MAX_PERSISTED_EVENTS`` per run.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import insert, select, update

from app.logger import logger
from app.utils.text import truncate
from app.web import files
from app.web.db import Database
from app.web.models import Run, RunEvent, utcnow
from app.web.schemas import event_out, iso


FLUSH_INTERVAL = 0.25
MAX_BATCH = 200
MAX_PERSISTED_EVENTS = 5000
SUBSCRIBER_QUEUE_SIZE = 1000
MAX_STRING_CHARS = 20000
MAX_ARTIFACT_BYTES = 15 * 1024 * 1024
MAX_CHANGED_PATHS = 200

IMMEDIATE_TYPES = frozenset(
    {
        "run.started",
        "run.status",
        "run.finished",
        "human.question",
        "human.answer",
        "final",
        "router.decision",
        "plan.created",
    }
)
# Event kinds still persisted after a run reached MAX_PERSISTED_EVENTS.
ESSENTIAL_PREFIXES = ("run.", "plan.", "human.")
ESSENTIAL_TYPES = frozenset({"final", "usage"})
# Events whose text is kept in full (the final answer is also stored as a message).
UNTRUNCATED_TYPES = frozenset({"final"})

FINISHED_EVENT = "run.finished"
WORKSPACE_EVENT = "workspace.changed"


class _Signal:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"<{self.name}>"


# Queue items telling a subscriber to stop: the run ended or the subscriber lagged.
CLOSED = _Signal("closed")
DROPPED = _Signal("dropped")
# Inbox item asking the consumer to diff the workspace.
_CHECK_WORKSPACE = _Signal("check-workspace")


def is_essential(event_type: str) -> bool:
    return event_type in ESSENTIAL_TYPES or event_type.startswith(ESSENTIAL_PREFIXES)


def _bounded(value: Any, limit: int) -> Any:
    """JSON-compatible copy of ``value`` with long strings truncated."""
    if isinstance(value, str):
        return truncate(value, limit)
    if isinstance(value, dict):
        return {str(key): _bounded(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, limit) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int)):
        return value
    return truncate(str(value), limit)


def _image_extension(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def _write_artifact(directory: Path, seq: int, image_b64: str) -> Optional[str]:
    """Decode and store a screenshot; returns the file name (None if invalid)."""
    try:
        data = base64.b64decode(image_b64, validate=False)
    except (binascii.Error, ValueError):
        return None
    extension = _image_extension(data)
    if extension is None or len(data) > MAX_ARTIFACT_BYTES:
        return None
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    name = f"{seq:06d}.{extension}"
    (directory / name).write_bytes(data)
    return name


def format_sse(event: Dict[str, Any]) -> str:
    """One Server-Sent Events message for a RunEvent dict."""
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {payload}\n\n"


async def load_events(
    db: Database, run_id: str, after: int, limit: int
) -> List[Dict[str, Any]]:
    """Persisted events with ``seq > after`` (ascending, at most ``limit``)."""
    async with db.session() as session:
        rows = await session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after)
            .order_by(RunEvent.seq)
            .limit(limit)
        )
        return [event_out(row) for row in rows]


@dataclass(eq=False)
class Subscription:
    """A live listener: ``backlog`` holds events emitted but not yet persisted when
    it subscribed; later events (and CLOSED/DROPPED) arrive on ``queue``."""

    queue: asyncio.Queue
    backlog: List[Dict[str, Any]] = field(default_factory=list)


class RunEventPipeline:
    """Event sink, persistence and broadcast hub of one run."""

    def __init__(
        self,
        run_id: str,
        db: Database,
        artifacts_dir: Path,
        *,
        max_persisted: Optional[int] = None,
        queue_size: Optional[int] = None,
    ):
        self.run_id = run_id
        self._db = db
        self._artifacts_dir = artifacts_dir
        self._seq = 0
        self._persisted = 0
        self._max_persisted = (
            MAX_PERSISTED_EVENTS if max_persisted is None else max_persisted
        )
        self._queue_size = SUBSCRIBER_QUEUE_SIZE if queue_size is None else queue_size
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._pending: List[Tuple[Dict[str, Any], datetime]] = []
        self._flush_deadline: Optional[float] = None
        self._subscribers: Set[Subscription] = set()
        self._workspace: Optional[Path] = None
        self._snapshot: Optional[files.Snapshot] = None
        self._accepting = True
        self._finished = False
        self._consumer: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def last_seq(self) -> int:
        return self._seq

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._consumer = self._loop.create_task(
            self._consume(), name=f"run-events-{self.run_id}"
        )

    # ----------------------------------------------------------------- input

    def emit(self, event_type: str, data: dict) -> None:
        """EventSink: queue an event (never raises, never blocks).

        Meant to be called on the event loop; calls from worker threads (code run
        via ``asyncio.to_thread`` inherits the run context) are handed over safely.
        """
        if not self._accepting:
            logger.debug(f"Run {self.run_id}: dropping late event {event_type}")
            return
        item = (event_type, dict(data or {}), utcnow())
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if on_loop or self._loop is None:
            self._inbox.put_nowait(item)
            return
        try:
            self._loop.call_soon_threadsafe(self._inbox.put_nowait, item)
        except RuntimeError:  # the loop is closed: the run is over
            pass

    async def track_workspace(self, workspace: Path) -> None:
        """Take the baseline snapshot used for ``workspace.changed`` diffs."""
        self._snapshot = await asyncio.to_thread(files.snapshot, workspace)
        self._workspace = workspace

    def request_workspace_check(self) -> None:
        """Diff the workspace after the events queued so far."""
        if self._accepting:
            self._inbox.put_nowait(_CHECK_WORKSPACE)

    async def close(self) -> None:
        """Stop accepting events, process the queued ones, flush and notify
        subscribers. Safe to call more than once."""
        if self._accepting:
            self._accepting = False
            self._inbox.put_nowait(None)
        if self._consumer is not None:
            await asyncio.shield(self._consumer)

    # ----------------------------------------------------------- subscribers

    def subscribe(self) -> Subscription:
        subscription = Subscription(
            queue=asyncio.Queue(maxsize=self._queue_size),
            backlog=[event for event, _ in self._pending],
        )
        if self._finished:
            subscription.queue.put_nowait(CLOSED)
        else:
            self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def unpersisted(self, after: int) -> List[Dict[str, Any]]:
        """Events with ``seq > after`` that are not in the database yet."""
        return [event for event, _ in self._pending if event["seq"] > after]

    def close_subscribers(self) -> None:
        """Disconnect all live subscribers (they can resume later)."""
        for subscription in list(self._subscribers):
            self._signal(subscription, CLOSED)
        self._subscribers.clear()

    @staticmethod
    def _signal(subscription: Subscription, signal: _Signal) -> None:
        queue = subscription.queue
        while True:
            try:
                queue.put_nowait(signal)
                return
            except asyncio.QueueFull:
                queue.get_nowait()

    def _broadcast(self, event: Dict[str, Any]) -> None:
        for subscription in list(self._subscribers):
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"Run {self.run_id}: dropping a slow event subscriber")
                self._subscribers.discard(subscription)
                self._signal(subscription, DROPPED)

    # -------------------------------------------------------------- consumer

    async def _consume(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                timeout = None
                if self._flush_deadline is not None:
                    timeout = max(self._flush_deadline - loop.time(), 0)
                try:
                    item = await asyncio.wait_for(self._inbox.get(), timeout)
                except TimeoutError:
                    await self._flush()
                    continue
                if item is None:
                    break
                try:
                    if item is _CHECK_WORKSPACE:
                        await self._check_workspace()
                        continue
                    event_type, data, ts = item
                    await self._process(event_type, data, ts)
                    if event_type == "tool.result":
                        await self._check_workspace()
                except Exception as e:  # keep the pipeline alive
                    logger.exception(f"Run {self.run_id}: event processing failed: {e}")
        finally:
            await self._flush()
            self._finished = True
            self.close_subscribers()

    async def _process(self, event_type: str, data: dict, ts: datetime) -> None:
        self._seq += 1
        seq = self._seq
        image_b64 = data.pop("image_b64", None)
        limit = 0 if event_type in UNTRUNCATED_TYPES else MAX_STRING_CHARS
        data = _bounded(data, limit)
        if isinstance(image_b64, str) and image_b64:
            name = await asyncio.to_thread(
                _write_artifact, self._artifacts_dir, seq, image_b64
            )
            if name:
                data["image_url"] = f"/api/runs/{self.run_id}/artifacts/{name}"
        event = {
            "seq": seq,
            "run_id": self.run_id,
            "type": event_type,
            "ts": iso(ts),
            "data": data,
        }
        self._broadcast(event)

        if self._persisted + len(self._pending) >= self._max_persisted and not (
            is_essential(event_type)
        ):
            return
        self._pending.append((event, ts))
        if event_type in IMMEDIATE_TYPES or len(self._pending) >= MAX_BATCH:
            await self._flush()
        elif self._flush_deadline is None:
            self._flush_deadline = asyncio.get_running_loop().time() + FLUSH_INTERVAL

    async def _check_workspace(self) -> None:
        if self._workspace is None:
            return
        current = await asyncio.to_thread(files.snapshot, self._workspace)
        previous, self._snapshot = self._snapshot, current
        if current is None or previous is None:
            if current is not previous:
                await self._process(
                    WORKSPACE_EVENT, {"paths": [], "truncated": True}, utcnow()
                )
            return
        changed = files.diff_snapshots(previous, current)
        if changed:
            data: Dict[str, Any] = {"paths": changed[:MAX_CHANGED_PATHS]}
            if len(changed) > MAX_CHANGED_PATHS:
                data["truncated"] = True
            await self._process(WORKSPACE_EVENT, data, utcnow())

    async def _flush(self) -> None:
        self._flush_deadline = None
        if not self._pending:
            return
        batch = list(self._pending)
        try:
            async with self._db.session() as session:
                await session.execute(
                    insert(RunEvent),
                    [
                        {
                            "run_id": self.run_id,
                            "seq": event["seq"],
                            "type": event["type"],
                            "ts": ts,
                            "data": event["data"],
                        }
                        for event, ts in batch
                    ],
                )
                await session.execute(
                    update(Run)
                    .where(Run.id == self.run_id)
                    .values(last_seq=batch[-1][0]["seq"])
                )
                await session.commit()
            self._persisted += len(batch)
        except Exception as e:
            logger.error(
                f"Run {self.run_id}: failed to persist {len(batch)} events: {e}"
            )
        finally:
            del self._pending[: len(batch)]
