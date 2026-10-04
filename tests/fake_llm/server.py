"""OpenAI-compatible HTTP server answering with the fake model (see ``script``).

Endpoints:

* ``POST /v1/chat/completions`` — streaming (SSE chunks) and non-streaming, with
  tool calls; any API key is accepted.
* ``GET /v1/models`` — the model list.
* ``GET /fake/page`` — a small HTML page for browsing tests.
* ``GET /fake/requests`` / ``DELETE /fake/requests`` — the log of recent requests
  (for debugging tests), also appended to ``--log-file`` as JSON lines.
* ``GET /health``.

Usage: ``python -m tests.fake_llm.server --port 8765 [--slow-seconds 2.5]``.
"""

import argparse
import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .script import Reply, ToolCall, message_text, respond, tool_names


MODEL_ID = "fake-gpt"
DEFAULT_SLOW_SECONDS = 2.5
DEFAULT_CHUNK_DELAY = 0.03
WORDS_PER_CHUNK = 3
REQUEST_LOG_SIZE = 500
LOG_PREVIEW_CHARS = 300

# A small web page for browsing tests (GET /fake/page).
TEST_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>OpenManus test page</title></head>
<body style="font-family: sans-serif; margin: 3rem; background: #eef2ff">
<h1>Hello from the test web site</h1>
<p>This page is served by the fake LLM server for browsing tests.</p>
<a href="https://example.com">A link</a>
</body>
</html>
"""

logger = logging.getLogger("fake_llm")


@dataclass(frozen=True)
class FakeLLMSettings:
    """Timing and logging of the fake server.

    Attributes:
        slow_seconds: Delay before replies marked ``slow``.
        chunk_delay: Pause between streamed chunks.
        log_file: JSON-lines file receiving every request summary (optional).
    """

    slow_seconds: float = DEFAULT_SLOW_SECONDS
    chunk_delay: float = DEFAULT_CHUNK_DELAY
    log_file: Optional[Path] = None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _tool_call_payload(call: ToolCall, index: int) -> Dict[str, Any]:
    return {
        "index": index,
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }


def _usage(messages: List[Dict[str, Any]], reply: Reply) -> Dict[str, int]:
    prompt = _estimate_tokens(json.dumps(messages, ensure_ascii=False))
    completion = _estimate_tokens(reply.content or "") + 20 * len(reply.tool_calls)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _completion(
    reply: Reply, model: str, usage: Dict[str, int], completion_id: str
) -> Dict[str, Any]:
    tool_calls = [
        {
            key: value
            for key, value in _tool_call_payload(call, i).items()
            if key != "index"
        }
        for i, call in enumerate(reply.tool_calls)
    ]
    message: Dict[str, Any] = {"role": "assistant", "content": reply.content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": usage,
    }


def _text_chunks(text: str) -> List[str]:
    """Split ``text`` into small pieces (a few words with their whitespace)."""
    tokens = re.findall(r"\s*\S+\s*", text) or [text]
    return [
        "".join(tokens[i : i + WORDS_PER_CHUNK])
        for i in range(0, len(tokens), WORDS_PER_CHUNK)
    ]


async def _stream(
    reply: Reply,
    model: str,
    usage: Dict[str, int],
    completion_id: str,
    chunk_delay: float,
) -> AsyncIterator[bytes]:
    created = int(time.time())

    def chunk(
        delta: Dict[str, Any], finish_reason: Optional[str] = None, **extra: Any
    ) -> bytes:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            **extra,
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    yield chunk({"role": "assistant", "content": ""})
    for piece in _text_chunks(reply.content or ""):
        await asyncio.sleep(chunk_delay)
        yield chunk({"content": piece})
    for index, call in enumerate(reply.tool_calls):
        yield chunk({"tool_calls": [_tool_call_payload(call, index)]})
    yield chunk({}, "tool_calls" if reply.tool_calls else "stop")
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
        "usage": usage,
    }
    yield f"data: {json.dumps(final)}\n\n".encode()
    yield b"data: [DONE]\n\n"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error"}},
        status_code=status,
    )


def _summary(body: Dict[str, Any], reply: Reply) -> Dict[str, Any]:
    messages = body.get("messages") or []
    last_user = next(
        (message_text(m) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    return {
        "ts": time.time(),
        "kind": reply.kind,
        "model": body.get("model"),
        "stream": bool(body.get("stream")),
        "tools": tool_names(body.get("tools")),
        "messages": len(messages),
        "last_user": last_user[-LOG_PREVIEW_CHARS:],
        "reply": {
            "content": (reply.content or "")[:LOG_PREVIEW_CHARS],
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in reply.tool_calls
            ],
            "slow": reply.slow,
        },
    }


def create_app(settings: Optional[FakeLLMSettings] = None) -> FastAPI:
    """The fake OpenAI-compatible application."""
    settings = settings or FakeLLMSettings()
    app = FastAPI(title="OpenManus fake LLM", docs_url=None, redoc_url=None)
    requests: Deque[Dict[str, Any]] = deque(maxlen=REQUEST_LOG_SIZE)
    app.state.requests = requests

    def record(entry: Dict[str, Any]) -> None:
        requests.append(entry)
        logger.info(
            "%s stream=%s tools=%s -> %s",
            entry["kind"],
            entry["stream"],
            ",".join(entry["tools"]) or "-",
            ",".join(call["name"] for call in entry["reply"]["tool_calls"]) or "text",
        )
        if settings.log_file is not None:
            with settings.log_file.open("a", encoding="utf-8") as log:
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> Dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "e2e"}
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "The request body is not valid JSON")
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not messages:
            return _error(400, "'messages' must be a non-empty list")

        reply = respond(messages, body.get("tools"))
        record(_summary(body, reply))
        if reply.error:
            return _error(400, reply.error)
        if reply.slow and settings.slow_seconds > 0:
            await asyncio.sleep(settings.slow_seconds)

        model = str(body.get("model") or MODEL_ID)
        usage = _usage(messages, reply)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        if body.get("stream"):
            return StreamingResponse(
                _stream(reply, model, usage, completion_id, settings.chunk_delay),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        return _completion(reply, model, usage, completion_id)

    @app.get("/fake/page", response_class=HTMLResponse)
    async def page() -> str:
        return TEST_PAGE

    @app.get("/fake/requests")
    async def logged_requests() -> List[Dict[str, Any]]:
        return list(requests)

    @app.delete("/fake/requests", status_code=204)
    async def clear_requests() -> None:
        requests.clear()

    return app


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--slow-seconds",
        type=float,
        default=DEFAULT_SLOW_SECONDS,
        help="delay of agent replies for requests that contain 'slow'",
    )
    parser.add_argument("--chunk-delay", type=float, default=DEFAULT_CHUNK_DELAY)
    parser.add_argument("--log-file", type=Path, help="append request logs (JSONL)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[fake-llm] %(message)s")
    settings = FakeLLMSettings(
        slow_seconds=args.slow_seconds,
        chunk_delay=args.chunk_delay,
        log_file=args.log_file,
    )
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_level="warning",
        ws="none",
    )


if __name__ == "__main__":
    main()
