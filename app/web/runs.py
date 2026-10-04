"""Run manager: executes agent runs as background tasks of the web server.

Every run is an ``asyncio`` task that waits for a global concurrency slot, binds a
:class:`~app.context.RunContext` (workspace, event sink, ask-human bridge), calls the
core ``run_task`` with the conversation history and records the outcome. The
manager keeps only in-memory state for active runs; the database holds everything
else, so the server must run as a single worker process.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openai import APIConnectionError, APIStatusError, OpenAIError
from sqlalchemy import func, select, update

from app.context import RunContext, prepare_workspace, reset_run, set_run
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.utils.text import truncate
from app.web.db import Database
from app.web.events import FINISHED_EVENT, RunEventPipeline, Subscription
from app.web.models import (
    ACTIVE_STATUSES,
    CANCELLED,
    COMPLETED,
    FAILED,
    RUNNING,
    WAITING_INPUT,
    Conversation,
    Message,
    Run,
    RunEvent,
    utcnow,
)
from app.web.settings import WebSettings


TaskRunner = Callable[..., Awaitable[str]]

HISTORY_TURNS = 10
HISTORY_TURN_CHARS = 4000
HUMAN_TIMEOUT_ANSWER = "The user did not answer in time."
CANCELLED_REPLY = "⏹"
SERVER_RESTARTED = "Server restarted"
SERVER_SHUT_DOWN = "Server shut down"
TIMEOUT_ERROR = "timeout"
MAX_ERROR_CHARS = 1000

# Errors of the model provider: logged without a traceback.
_EXPECTED_ERRORS = (OpenAIError, TokenLimitExceeded)

_USER_CANCEL = "user"
_SHUTDOWN_CANCEL = "shutdown"


class RunRejected(Exception):
    """A run cannot be started now; ``status_code`` is the HTTP answer."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class _Question:
    question_id: str
    answer: asyncio.Future


@dataclass(eq=False)
class _ActiveRun:
    run_id: str
    user_id: str
    conversation_id: str
    pipeline: Optional[RunEventPipeline] = None
    task: Optional[asyncio.Task] = None
    question: Optional[_Question] = None
    question_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_reason: Optional[str] = None
    finishing: bool = False


# Core modules imported by the first run (agents are created lazily by the
# registry); importing them ahead of time keeps that run from stalling the loop.
_CORE_MODULES = (
    "app.flow.runner",
    "app.agent.manus",
    "app.agent.browser",
    "app.agent.researcher",
    "app.agent.swe",
    "app.agent.data_analysis",
    "app.agent.writer",
)


def _default_task_runner() -> TaskRunner:
    from app.flow.runner import run_task

    return run_task


def _import_core_modules() -> None:
    for name in _CORE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # an optimisation only; the run reports real errors
            logger.warning(f"Could not preload {name}: {e}")


def _describe_error(error: BaseException) -> str:
    """Short user-facing description of a run failure."""
    if isinstance(error, APIStatusError):
        body = error.body
        detail = body.get("message") if isinstance(body, dict) else None
        text = f"The model provider returned an error (HTTP {error.status_code})"
        return truncate(f"{text}: {detail}" if detail else text, MAX_ERROR_CHARS)
    if isinstance(error, APIConnectionError):
        return truncate(f"Could not reach the model provider: {error}", MAX_ERROR_CHARS)
    text = str(error).strip()
    name = type(error).__name__
    return truncate(f"{name}: {text}" if text else name, MAX_ERROR_CHARS)


def _assistant_reply(status: str, error: Optional[str], timeout: float) -> str:
    """Assistant message recorded for a run that did not complete."""
    if status == CANCELLED:
        return CANCELLED_REPLY
    if error == TIMEOUT_ERROR:
        return f"⚠️ Time limit exceeded ({int(timeout)} s)"
    return f"⚠️ {error or 'Unknown error'}"


class RunManager:
    """Starts, tracks, cancels and finalizes agent runs."""

    def __init__(
        self,
        settings: WebSettings,
        db: Database,
        task_runner: Optional[TaskRunner] = None,
    ):
        self.settings = settings
        self.db = db
        self._task_runner = task_runner
        self._slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self._runs: Dict[str, _ActiveRun] = {}
        self._by_conversation: Dict[str, str] = {}
        self._closing = False

    async def preload(self) -> None:
        """Import the core agent modules in a worker thread (default runner only)."""
        if self._task_runner is None:
            await asyncio.to_thread(_import_core_modules)

    @property
    def task_runner(self) -> TaskRunner:
        if self._task_runner is None:
            self._task_runner = _default_task_runner()
        return self._task_runner

    # --------------------------------------------------------------- queries

    @property
    def active_count(self) -> int:
        return len(self._runs)

    def active_run_id(self, conversation_id: str) -> Optional[str]:
        return self._by_conversation.get(conversation_id)

    def live_seq(self, run_id: str) -> Optional[int]:
        active = self._runs.get(run_id)
        if active is None or active.pipeline is None:
            return None
        return active.pipeline.last_seq

    def subscribe(self, run_id: str) -> Optional[Subscription]:
        """Live event subscription of an active run (None when not active)."""
        active = self._runs.get(run_id)
        if active is None or active.pipeline is None:
            return None
        return active.pipeline.subscribe()

    def unsubscribe(self, run_id: str, subscription: Subscription) -> None:
        active = self._runs.get(run_id)
        if active is not None and active.pipeline is not None:
            active.pipeline.unsubscribe(subscription)

    def unpersisted_events(self, run_id: str, after: int) -> List[Dict[str, Any]]:
        active = self._runs.get(run_id)
        if active is None or active.pipeline is None:
            return []
        return active.pipeline.unpersisted(after)

    # ------------------------------------------------------------- lifecycle

    async def recover(self) -> int:
        """Mark runs left active by a previous process as failed; returns the count."""
        now = utcnow()
        async with self.db.session() as session:
            stale = list(
                await session.scalars(
                    select(Run).where(Run.status.in_(ACTIVE_STATUSES))
                )
            )
            for run in stale:
                last_seq = await session.scalar(
                    select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id)
                )
                seq = max(last_seq or 0, run.last_seq or 0) + 1
                started = run.started_at or run.created_at
                run.status = FAILED
                run.error = SERVER_RESTARTED
                run.finished_at = now
                run.pending_question = None
                run.last_seq = seq
                session.add(
                    RunEvent(
                        run_id=run.id,
                        seq=seq,
                        type=FINISHED_EVENT,
                        ts=now,
                        data={
                            "status": FAILED,
                            "error": SERVER_RESTARTED,
                            "duration_ms": int((now - started).total_seconds() * 1000),
                            "usage": run.usage,
                        },
                    )
                )
                session.add(
                    Message(
                        conversation_id=run.conversation_id,
                        role="assistant",
                        content=_assistant_reply(FAILED, SERVER_RESTARTED, 0),
                        run_id=run.id,
                        attachments=[],
                        created_at=now,
                    )
                )
            await session.commit()
        if stale:
            logger.warning(f"Marked {len(stale)} interrupted run(s) as failed")
        return len(stale)

    def begin_shutdown(self) -> None:
        """Refuse new runs and disconnect live event streams."""
        self._closing = True
        for active in list(self._runs.values()):
            if active.pipeline is not None:
                active.pipeline.close_subscribers()

    async def shutdown(self, timeout: float = 30) -> None:
        """Cancel all runs and wait (up to ``timeout``) for them to finish."""
        self.begin_shutdown()
        tasks = []
        for active in list(self._runs.values()):
            if active.task is not None:
                self._cancel(active, _SHUTDOWN_CANCEL)
                tasks.append(active.task)
        if not tasks:
            return
        logger.info(f"Stopping {len(tasks)} active run(s)")
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning(f"{len(pending)} run(s) did not stop within {timeout} s")

    # ---------------------------------------------------------------- start

    def reserve(self, user_id: str, conversation_id: str) -> str:
        """Claim a run slot for a conversation; returns the new run id.

        Must be followed by :meth:`launch` (after the run row is committed) or
        :meth:`release`.

        Raises:
            RunRejected: 503 while shutting down, 409 when the conversation already
                has an active run, 429 when the user or server limit is reached.
        """
        if self._closing:
            raise RunRejected(503, "The server is shutting down")
        if conversation_id in self._by_conversation:
            raise RunRejected(409, "A run is already active in this conversation")
        user_runs = sum(1 for run in self._runs.values() if run.user_id == user_id)
        if user_runs >= self.settings.max_runs_per_user:
            raise RunRejected(
                429,
                f"You already have {user_runs} active run(s); "
                "wait for one to finish or stop it",
            )
        capacity = self.settings.max_concurrent_runs + self.settings.max_queued_runs
        if len(self._runs) >= capacity:
            raise RunRejected(429, "The server is busy; try again in a few minutes")
        run_id = uuid.uuid4().hex
        self._runs[run_id] = _ActiveRun(run_id, user_id, conversation_id)
        self._by_conversation[conversation_id] = run_id
        return run_id

    def release(self, run_id: str) -> None:
        """Give up a reservation that was not launched."""
        active = self._runs.get(run_id)
        if active is not None and active.task is None:
            self._unregister(active)

    def launch(
        self,
        run_id: str,
        *,
        request: str,
        mode: str,
        attachments: List[str],
        request_message_id: str,
    ) -> None:
        """Start the background task of a reserved run."""
        active = self._runs[run_id]
        active.pipeline = RunEventPipeline(
            run_id, self.db, self.settings.run_artifacts(run_id)
        )
        active.pipeline.start()
        active.task = asyncio.create_task(
            self._execute(active, request, mode, attachments, request_message_id),
            name=f"run-{run_id}",
        )

    # ---------------------------------------------------------- interaction

    def answer(self, run_id: str, question_id: str, answer: str) -> bool:
        """Deliver the user's answer to a pending question."""
        active = self._runs.get(run_id)
        question = active.question if active is not None else None
        if (
            question is None
            or question.question_id != question_id
            or question.answer.done()
        ):
            return False
        question.answer.set_result(answer)
        return True

    async def cancel(self, run_id: str, wait: float = 0) -> bool:
        """Request cancellation; optionally wait up to ``wait`` seconds for the end."""
        active = self._runs.get(run_id)
        if active is None or active.task is None:
            return False
        self._cancel(active, _USER_CANCEL)
        if wait > 0:
            await asyncio.wait({active.task}, timeout=wait)
        return True

    async def cancel_where(
        self,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        wait: float = 30,
    ) -> None:
        """Cancel the active runs of a user or conversation and wait for them."""
        tasks = []
        for active in list(self._runs.values()):
            if (user_id is None or active.user_id == user_id) and (
                conversation_id is None or active.conversation_id == conversation_id
            ):
                if active.task is not None:
                    self._cancel(active, _USER_CANCEL)
                    tasks.append(active.task)
        if tasks:
            await asyncio.wait(tasks, timeout=wait)

    @staticmethod
    def _cancel(active: _ActiveRun, reason: str) -> None:
        task = active.task
        if task is None or task.done() or active.finishing or task.cancelling():
            return
        active.cancel_reason = reason
        task.cancel()

    def _unregister(self, active: _ActiveRun) -> None:
        self._runs.pop(active.run_id, None)
        if self._by_conversation.get(active.conversation_id) == active.run_id:
            del self._by_conversation[active.conversation_id]

    # ------------------------------------------------------------ execution

    async def _update_run(self, run_id: str, **values: Any) -> None:
        async with self.db.session() as session:
            await session.execute(update(Run).where(Run.id == run_id).values(**values))
            await session.commit()

    async def _history(
        self, conversation_id: str, request_message_id: str
    ) -> List[Dict[str, str]]:
        """Prior turns of the conversation (answers of unfinished runs excluded)."""
        async with self.db.session() as session:
            request = await session.get(Message, request_message_id)
            if request is None:
                return []
            rows = (
                await session.execute(
                    select(Message.role, Message.content, Run.status)
                    .outerjoin(Run, Run.id == Message.run_id)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.created_at < request.created_at,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(HISTORY_TURNS * 3)
                )
            ).all()
        turns = [
            {"role": role, "content": truncate(content, HISTORY_TURN_CHARS)}
            for role, content, status in reversed(rows)
            if role == "user" or status == COMPLETED
        ]
        return turns[-HISTORY_TURNS:]

    async def _ask_human(self, active: _ActiveRun, question: str) -> str:
        """HumanInputProvider bridge: publish the question and wait for the answer."""
        async with active.question_lock:
            pipeline = active.pipeline
            pending = {"question_id": uuid.uuid4().hex, "question": question}
            future = asyncio.get_running_loop().create_future()
            active.question = _Question(pending["question_id"], future)
            try:
                await self._update_run(
                    active.run_id, status=WAITING_INPUT, pending_question=pending
                )
                pipeline.emit("human.question", pending)
                pipeline.emit("run.status", {"status": WAITING_INPUT})
                try:
                    answer = await asyncio.wait_for(
                        future, self.settings.human_input_timeout
                    )
                except TimeoutError:
                    answer = HUMAN_TIMEOUT_ANSWER
            finally:
                active.question = None
            await self._update_run(active.run_id, status=RUNNING, pending_question=None)
            pipeline.emit(
                "human.answer",
                {"question_id": pending["question_id"], "answer": answer},
            )
            pipeline.emit("run.status", {"status": RUNNING})
            return answer

    async def _execute(
        self,
        active: _ActiveRun,
        request: str,
        mode: str,
        attachments: List[str],
        request_message_id: str,
    ) -> None:
        pipeline = active.pipeline
        status, error, answer = FAILED, None, None
        started: Optional[datetime] = None
        context: Optional[RunContext] = None
        deadline: Optional[asyncio.Timeout] = None
        cancelled = False
        try:
            async with self._slots:
                started = utcnow()
                await self._update_run(
                    active.run_id, status=RUNNING, started_at=started
                )
                pipeline.emit("run.started", {"mode": mode})
                pipeline.emit("run.status", {"status": RUNNING})
                workspace = await asyncio.to_thread(
                    prepare_workspace,
                    self.settings.workspace_path(
                        active.user_id, active.conversation_id
                    ),
                )
                await pipeline.track_workspace(workspace)
                history = await self._history(
                    active.conversation_id, request_message_id
                )
                context = RunContext(
                    run_id=active.run_id,
                    workspace=workspace,
                    emit_sink=pipeline.emit,
                    ask_human=functools.partial(self._ask_human, active),
                )
                token = set_run(context)
                deadline = asyncio.timeout(self.settings.run_timeout)
                try:
                    async with deadline:
                        answer = await self.task_runner(
                            request, mode=mode, history=history, attachments=attachments
                        )
                finally:
                    reset_run(token)
            status = COMPLETED
        except TimeoutError as e:
            expired = deadline is not None and deadline.expired()
            error = TIMEOUT_ERROR if expired else _describe_error(e)
        except asyncio.CancelledError:
            cancelled = True
            if active.cancel_reason == _SHUTDOWN_CANCEL:
                error = SERVER_SHUT_DOWN
            else:
                status = CANCELLED
        except ValueError as e:  # user-facing (e.g. unknown or unavailable mode)
            error = truncate(str(e) or "Invalid request", MAX_ERROR_CHARS)
        except Exception as e:
            error = _describe_error(e)
            if isinstance(e, _EXPECTED_ERRORS):
                logger.error(f"Run {active.run_id} failed: {error}")
            else:
                logger.exception(f"Run {active.run_id} failed: {error}")

        try:
            await self._finish(active, status, error, answer, started, context)
        finally:
            self._unregister(active)
        if cancelled:
            raise asyncio.CancelledError

    async def _finish(
        self,
        active: _ActiveRun,
        status: str,
        error: Optional[str],
        answer: Optional[str],
        started: Optional[datetime],
        context: Optional[RunContext],
    ) -> None:
        """Record the outcome: assistant message, run row, ``run.finished``."""
        active.finishing = True
        pipeline = active.pipeline
        finished = utcnow()
        usage = dict(context.usage) if context is not None else None
        if status == COMPLETED:
            content = answer if isinstance(answer, str) and answer.strip() else "…"
        else:
            content = _assistant_reply(status, error, self.settings.run_timeout)
        try:
            async with self.db.session() as session:
                await session.execute(
                    update(Run)
                    .where(Run.id == active.run_id)
                    .values(
                        status=status,
                        error=error,
                        finished_at=finished,
                        usage=usage,
                        pending_question=None,
                    )
                )
                session.add(
                    Message(
                        conversation_id=active.conversation_id,
                        role="assistant",
                        content=content,
                        run_id=active.run_id,
                        attachments=[],
                        created_at=finished,
                    )
                )
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == active.conversation_id)
                    .values(updated_at=finished)
                )
                await session.commit()
        except Exception as e:
            logger.exception(f"Run {active.run_id}: failed to record the result: {e}")

        duration = (finished - (started or finished)).total_seconds()
        finished_data: Dict[str, Any] = {
            "status": status,
            "duration_ms": int(duration * 1000),
            "usage": usage,
        }
        if error:
            finished_data["error"] = error
        pipeline.request_workspace_check()
        pipeline.emit(FINISHED_EVENT, finished_data)
        await pipeline.close()
        logger.info(
            f"Run {active.run_id} {status}"
            + (f" ({error})" if error else "")
            + f" in {duration:.1f} s"
        )
