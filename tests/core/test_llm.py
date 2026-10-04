import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
import tiktoken
from openai import AsyncOpenAI, AuthenticationError
from tenacity import wait_none

from app.config import LLMSettings
from app.exceptions import TokenLimitExceeded
from app.llm import LLM, TOOL_IMAGES_NOTE, is_reasoning_model, model_supports_images
from app.schema import Message
from app.utils.images import detect_image_mime
from app.utils.tokenizer import ApproximateTokenizer, get_tokenizer


BASE_URL = "https://llm.test/v1"
COMPLETIONS_URL = f"{BASE_URL}/chat/completions"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def make_llm(model: str = "gpt-4o-mini", **overrides) -> LLM:
    """An uncached LLM whose client never retries on its own (fast tests)."""
    settings = LLMSettings(
        model=model,
        base_url=BASE_URL,
        api_key="test-key",
        api_type="",
        api_version="",
        **overrides,
    )
    llm = LLM(llm_config=settings)
    llm.client = AsyncOpenAI(api_key="test-key", base_url=BASE_URL, max_retries=0)
    return llm


def completion(content: str = "hello") -> dict:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


def sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (body + "data: [DONE]\n\n").encode()


def chunk(content=None, usage=None, choices=True) -> dict:
    data = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": (
            [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
            if choices
            else []
        ),
    }
    if usage:
        data["usage"] = usage
    return data


@pytest.fixture
def fast_retries(monkeypatch):
    for method in (LLM.ask, LLM.ask_tool, LLM.ask_with_images):
        monkeypatch.setattr(method.retry, "wait", wait_none())


@pytest.mark.asyncio
@respx.mock
async def test_ask_streams_deltas_and_skips_chunks_without_choices(run_context):
    respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                chunk(choices=False),  # e.g. Azure prompt_filter_results
                chunk("Hel"),
                chunk("lo"),
                chunk(None),
                chunk(
                    choices=False,
                    usage={
                        "prompt_tokens": 11,
                        "completion_tokens": 3,
                        "total_tokens": 14,
                    },
                ),
            ),
        )
    )
    deltas = []
    result = await make_llm().ask([Message.user_message("hi")], on_delta=deltas.append)

    assert result == "Hello"
    assert deltas == ["Hel", "lo"]
    assert run_context.usage == {"input_tokens": 11, "completion_tokens": 3}
    request = json.loads(respx.calls.last.request.content)
    assert request["stream"] is True


@pytest.mark.asyncio
@respx.mock
async def test_ask_is_not_streamed_in_a_run_without_callback(run_context):
    respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=completion("ok"))
    )
    assert await make_llm().ask([Message.user_message("hi")]) == "ok"
    assert json.loads(respx.calls.last.request.content)["stream"] is False
    assert run_context.usage == {"input_tokens": 12, "completion_tokens": 4}


@pytest.mark.asyncio
@respx.mock
async def test_transient_errors_are_retried(fast_retries, config_dir):
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(200, json=completion("recovered")),
        ]
    )
    assert (
        await make_llm().ask([Message.user_message("hi")], stream=False) == "recovered"
    )
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_non_transient_errors_are_raised_directly(fast_retries, config_dir):
    route = respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(AuthenticationError):
        await make_llm().ask_tool([Message.user_message("hi")])
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_token_limit_is_raised_before_any_request(config_dir):
    llm = make_llm(max_input_tokens=1)
    with pytest.raises(TokenLimitExceeded):
        await llm.ask_tool([Message.user_message("a fairly long message " * 10)])


@pytest.mark.asyncio
@respx.mock
async def test_reasoning_models_use_max_completion_tokens(config_dir):
    respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=completion())
    )
    await make_llm("o3-mini").ask([Message.user_message("hi")], stream=False)
    request = json.loads(respx.calls.last.request.content)
    assert "max_completion_tokens" in request
    assert "max_tokens" not in request and "temperature" not in request


@pytest.mark.asyncio
async def test_provider_concurrency_is_limited(monkeypatch, config_dir):
    monkeypatch.setenv("OPENMANUS_LLM_MAX_CONCURRENCY", "2")
    active = peak = 0

    async def create(**params):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"))], usage=None
        )

    llm = make_llm(model="gpt-4o-mini-concurrency")
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    await asyncio.gather(
        *(llm.ask([Message.user_message("hi")], stream=False) for _ in range(6))
    )
    assert peak == 2


@pytest.mark.parametrize(
    "model, expected",
    [
        ("gpt-4o-mini", True),
        ("gpt-4.1", True),
        ("gpt-5", True),
        ("o4-mini", True),
        ("openai/o3", True),
        ("claude-3-7-sonnet-20250219", True),
        ("claude-sonnet-4-5", True),
        ("claude-opus-4-1", True),
        ("us.anthropic.claude-3-5-haiku-20241022-v1:0", True),
        ("gemini-2.5-pro", True),
        ("llama3.2-vision", True),
        ("qwen2.5-vl-72b-instruct", True),
        ("llava:13b", True),
        ("gpt-3.5-turbo", False),
        ("deepseek-chat", False),
        ("llama3.2", False),
    ],
)
def test_supports_images_heuristic(model, expected):
    assert model_supports_images(model) is expected


@pytest.mark.parametrize(
    "model, expected",
    [
        ("o1", True),
        ("o1-mini", True),
        ("o3-mini", True),
        ("o4-mini", True),
        ("gpt-5-mini", True),
        ("gpt-4o", False),
        ("claude-3-opus", False),
    ],
)
def test_reasoning_model_detection(model, expected):
    assert is_reasoning_model(model) is expected


def test_supports_images_setting_overrides_heuristic(config_dir):
    assert make_llm("deepseek-chat", supports_images=True).supports_images is True
    assert make_llm("gpt-4o", supports_images=False).supports_images is False


@pytest.mark.parametrize(
    "data, mime",
    [
        (PNG_BYTES, "image/png"),
        (JPEG_BYTES, "image/jpeg"),
        (GIF_BYTES, "image/gif"),
        (WEBP_BYTES, "image/webp"),
        (b"plain", "image/jpeg"),
    ],
)
def test_detect_image_mime(data, mime):
    assert detect_image_mime(data) == mime
    assert detect_image_mime(base64.b64encode(data).decode()) == mime


def test_format_messages_places_images_correctly():
    png = base64.b64encode(PNG_BYTES).decode()
    jpeg = base64.b64encode(JPEG_BYTES).decode()
    original = {"role": "user", "content": "look", "base64_image": png}
    messages = [
        original,
        Message.from_tool_calls(
            tool_calls=[
                SimpleNamespace(
                    id="a",
                    function=SimpleNamespace(
                        model_dump=lambda: {"name": "t", "arguments": "{}"}
                    ),
                ),
                SimpleNamespace(
                    id="b",
                    function=SimpleNamespace(
                        model_dump=lambda: {"name": "t", "arguments": "{}"}
                    ),
                ),
            ]
        ),
        Message.tool_message("first", name="t", tool_call_id="a", base64_image=jpeg),
        Message.tool_message("second", name="t", tool_call_id="b"),
        Message.assistant_message("done"),
    ]

    formatted = LLM.format_messages(messages, supports_images=True)

    assert original == {
        "role": "user",
        "content": "look",
        "base64_image": png,
    }  # not mutated
    assert formatted[0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert [m["role"] for m in formatted] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "user",
        "assistant",
    ]
    assert formatted[2]["content"] == "first" and "base64_image" not in formatted[2]
    images_message = formatted[4]["content"]
    assert images_message[0]["text"] == TOOL_IMAGES_NOTE
    assert images_message[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    text_only = LLM.format_messages(messages, supports_images=False)
    assert [m["role"] for m in text_only] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert all("base64_image" not in m for m in text_only)


def test_tokenizer_falls_back_when_encoding_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(tiktoken, "encoding_for_model", unavailable)
    monkeypatch.setattr(tiktoken, "get_encoding", unavailable)
    get_tokenizer.cache_clear()
    try:
        tokenizer = get_tokenizer("gpt-4o-offline-test")
        assert isinstance(tokenizer, ApproximateTokenizer)
        assert tokenizer.count("abcdefgh") == 2
        assert tokenizer.count("") == 0
    finally:
        get_tokenizer.cache_clear()


def test_tiktoken_tokenizer_accepts_special_tokens():
    tokenizer = get_tokenizer("gpt-4o")
    assert tokenizer.count("text with <|endoftext|> inside") > 0
