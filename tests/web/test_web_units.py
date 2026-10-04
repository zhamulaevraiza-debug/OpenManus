"""Unit tests of web backend building blocks (no HTTP)."""

import asyncio
import os
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from openai import APIConnectionError, BadRequestError

from app.web import files
from app.web.db import Database
from app.web.events import CLOSED, DROPPED, RunEventPipeline, load_events
from app.web.models import Conversation, Run, User
from app.web.runs import _describe_error
from app.web.security import LoginThrottle, TokenService
from app.web.settings import WebSettings


def test_sanitize_filename():
    assert files.sanitize_filename("../../etc/passwd") == "passwd"
    assert files.sanitize_filename("C:\\Users\\x\\report.docx") == "report.docx"
    assert files.sanitize_filename(".bashrc") == "bashrc"
    assert files.sanitize_filename('a<b>c:d"e|f?g*h') == "a_b_c_d_e_f_g_h"
    assert files.sanitize_filename("...") == "file"
    assert files.sanitize_filename("tab\tname\x00.txt") == "tab_name_.txt"
    sanitized = files.sanitize_filename("я" * 300 + ".txt")
    assert sanitized.endswith(".txt") and len(sanitized.encode()) <= 200


def test_resolve_path_confinement(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert files.resolve_path(workspace, "a/b.txt") == workspace / "a" / "b.txt"
    assert files.resolve_path(workspace, "") == workspace
    for bad in ("../x", "/etc/passwd", "a/../../x", "C:/x", "a\x00b"):
        with pytest.raises(files.PathViolation):
            files.resolve_path(workspace, bad)
    with pytest.raises(files.PathViolation):
        files.resolve_entry(workspace, "")


def test_snapshot_diff_and_limit(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "a.txt").write_text("a")
    (workspace / "sub" / "b.txt").write_text("b")
    (workspace / ".hidden").write_text("h")
    before = files.snapshot(workspace)
    assert set(before) == {"a.txt", "sub/b.txt"}
    (workspace / "a.txt").write_text("changed!")
    (workspace / "sub" / "b.txt").unlink()
    (workspace / "c.txt").write_text("c")
    assert files.diff_snapshots(before, files.snapshot(workspace)) == [
        "a.txt",
        "c.txt",
        "sub/b.txt",
    ]
    assert files.snapshot(workspace, limit=1) is None
    assert files.snapshot(tmp_path / "missing") == {}


def test_login_throttle_window(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.web.security.time.monotonic", lambda: clock[0])
    throttle = LoginThrottle(max_failures=2, window_seconds=10, max_keys=2)
    throttle.record_failure("1.1.1.1", "Bob")
    throttle.record_failure("1.1.1.1", "bob")
    assert throttle.is_blocked("1.1.1.1", "BOB")
    assert not throttle.is_blocked("2.2.2.2", "bob")
    clock[0] += 11
    assert not throttle.is_blocked("1.1.1.1", "bob")
    for index in range(5):
        throttle.record_failure(f"10.0.0.{index}", "x")
    assert len(throttle._failures) == 2  # bounded memory


def test_tokens_expire_and_reject_tampering():
    tokens = TokenService("k" * 48, session_days=1)
    token = tokens.issue("user-1", 3)
    claims = tokens.verify(token)
    assert claims.user_id == "user-1" and claims.password_version == 3
    assert tokens.verify(token[:-2] + "xx") is None
    assert TokenService("other" * 10, 1).verify(token) is None
    expired = TokenService("k" * 48, session_days=-1).issue("user-1", 3)
    assert tokens.verify(expired) is None


def test_settings_from_env(tmp_path: Path):
    settings = WebSettings.from_env(
        {
            "OPENMANUS_DATA_DIR": str(tmp_path),
            "OPENMANUS_MAX_CONCURRENT_RUNS": "3",
            "OPENMANUS_ALLOW_REGISTRATION": "yes",
            "OPENMANUS_COOKIE_SECURE": "true",
            "OPENMANUS_CORS_ORIGINS": "https://a.example/, https://b.example",
            "OPENMANUS_ADMIN_USERNAME": "Root",
            "OPENMANUS_RUN_TIMEOUT": "90.5",
        }
    )
    assert settings.data_dir == tmp_path.resolve()
    assert settings.database_url == f"sqlite+aiosqlite:///{tmp_path / 'openmanus.db'}"
    assert settings.max_concurrent_runs == 3 and settings.allow_registration is True
    assert settings.cookie_secure == "true" and settings.admin_username == "root"
    assert settings.cors_origins == ("https://a.example", "https://b.example")
    assert settings.run_timeout == 90.5 and settings.max_runs_per_user == 2
    for bad in (
        {"OPENMANUS_PORT": "http"},
        {"OPENMANUS_MAX_RUNS_PER_USER": "0"},
        {"OPENMANUS_COOKIE_SECURE": "sometimes"},
        {"OPENMANUS_TRUST_PROXY": "maybe"},
    ):
        with pytest.raises(ValueError):
            WebSettings.from_env({"OPENMANUS_DATA_DIR": str(tmp_path), **bad})


def test_secret_key_is_generated_once(tmp_path: Path):
    settings = WebSettings.from_env({"OPENMANUS_DATA_DIR": str(tmp_path)})
    settings.ensure_dirs()
    key = settings.load_secret_key()
    assert len(key) > 40 and settings.load_secret_key() == key
    assert os.stat(settings.secret_key_file).st_mode & 0o777 == 0o600
    explicit = settings.with_overrides(secret_key="from-env")
    assert explicit.load_secret_key() == "from-env"


@pytest_asyncio.fixture
async def pipeline_db(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")
    await db.create_all()
    async with db.session() as session:
        user = User(username="u", password_hash="x")
        session.add(user)
        await session.flush()
        conversation = Conversation(user_id=user.id)
        session.add(conversation)
        await session.flush()
        run = Run(conversation_id=conversation.id, user_id=user.id, mode="auto")
        session.add(run)
        await session.commit()
        run_id = run.id
    yield db, run_id
    await db.dispose()


@pytest.mark.asyncio
async def test_pipeline_backlog_and_slow_subscriber(pipeline_db, tmp_path):
    db, run_id = pipeline_db
    pipeline = RunEventPipeline(run_id, db, tmp_path / "artifacts", queue_size=3)
    pipeline.start()
    pipeline.emit("agent.step", {"step": 1})
    pipeline.emit("agent.step", {"step": 2})
    await asyncio.sleep(0.05)  # processed, not yet flushed (batched)
    late = pipeline.subscribe()
    assert [event["seq"] for event in late.backlog] == [1, 2]
    assert [event["seq"] for event in pipeline.unpersisted(1)] == [2]

    slow = pipeline.subscribe()
    for step in range(3, 8):
        pipeline.emit("agent.step", {"step": step})
    await asyncio.sleep(0.05)
    items = []
    while not slow.queue.empty():
        items.append(slow.queue.get_nowait())
    # The oldest queued events make room for the signal; order is preserved.
    assert items[-1] is DROPPED
    assert [event["seq"] for event in items[:-1]] == [4, 5]

    follower = pipeline.subscribe()
    await pipeline.close()
    assert follower.queue.get_nowait() is CLOSED
    assert pipeline.last_seq == 7
    stored = await load_events(db, run_id, 0, 100)
    assert [event["seq"] for event in stored] == list(range(1, 8))
    assert pipeline.unpersisted(0) == []
    # Events after close are ignored; new subscribers are told the stream ended.
    pipeline.emit("agent.step", {"step": 99})
    assert pipeline.subscribe().queue.get_nowait() is CLOSED


@pytest.mark.asyncio
async def test_pipeline_bounds_strings_and_serializes_values(pipeline_db, tmp_path):
    db, run_id = pipeline_db
    pipeline = RunEventPipeline(run_id, db, tmp_path / "artifacts")
    pipeline.start()
    pipeline.emit("tool.call", {"arguments": {"text": "x" * 50000}, "path": Path("/a")})
    pipeline.emit("final", {"content": "y" * 50000})
    await pipeline.close()
    call, final = await load_events(db, run_id, 0, 10)
    assert len(call["data"]["arguments"]["text"]) <= 20000
    assert call["data"]["path"] == "/a"
    assert final["data"]["content"] == "y" * 50000


@pytest.mark.asyncio
async def test_pipeline_accepts_events_from_worker_threads(pipeline_db, tmp_path):
    db, run_id = pipeline_db
    pipeline = RunEventPipeline(run_id, db, tmp_path / "artifacts")
    pipeline.start()
    pipeline.emit("agent.step", {"step": 1})
    await asyncio.to_thread(pipeline.emit, "tool.result", {"output": "from thread"})
    await asyncio.sleep(0.01)
    pipeline.emit("usage", {"input_tokens": float("nan")})
    await pipeline.close()
    stored = await load_events(db, run_id, 0, 10)
    assert [event["type"] for event in stored] == ["agent.step", "tool.result", "usage"]
    assert stored[2]["data"] == {"input_tokens": None}


def test_model_provider_errors_are_described_readably():
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    response = httpx.Response(400, request=request)
    error = BadRequestError(
        "Error code: 400 - {...}", response=response, body={"message": "Bad model"}
    )
    assert _describe_error(error) == (
        "The model provider returned an error (HTTP 400): Bad model"
    )
    assert _describe_error(APIConnectionError(request=request)) == (
        "Could not reach the model provider: Connection error."
    )
    assert _describe_error(RuntimeError("boom")) == "RuntimeError: boom"
