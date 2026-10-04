import base64
import json
import threading

import pytest

from app.bedrock import ChatCompletions


def test_message_conversion_handles_parallel_tool_calls_and_images():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nxxxx").decode()
    messages = [
        {"role": "system", "content": "sys one"},
        {"role": "system", "content": "sys two"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{png}"},
                },
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "t1", "arguments": '{"x": 1}'},
                },
                {
                    "id": "b",
                    "type": "function",
                    "function": {"name": "t2", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
    ]
    system, converted = ChatCompletions._convert_openai_messages_to_bedrock_format(
        messages
    )

    assert system == [{"text": "sys one"}, {"text": "sys two"}]
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
    assert converted[0]["content"][1]["image"]["format"] == "png"
    tool_uses = [block["toolUse"] for block in converted[1]["content"]]
    assert [(u["toolUseId"], u["name"], u["input"]) for u in tool_uses] == [
        ("a", "t1", {"x": 1}),
        ("b", "t2", {}),
    ]
    results = [block["toolResult"] for block in converted[2]["content"]]
    assert [(r["toolUseId"], r["content"][0]["text"]) for r in results] == [
        ("a", "ra"),
        ("b", "rb"),
    ]


class FakeBedrock:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.thread = None

    def converse(self, **request):
        self.requests.append(request)
        self.thread = threading.current_thread()
        return self.response


@pytest.mark.asyncio
async def test_create_runs_in_a_thread_and_maps_tool_calls():
    client = FakeBedrock(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "calling"},
                        {
                            "toolUse": {
                                "toolUseId": "u1",
                                "name": "search",
                                "input": {"q": "x"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
        }
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            },
        }
    ]
    response = await ChatCompletions(client).create(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10,
        temperature=0.5,
        tools=tools,
        tool_choice="required",
        timeout=30,
    )

    assert client.thread is not threading.main_thread()
    request = client.requests[0]
    assert request["toolConfig"]["toolChoice"] == {"any": {}}
    assert request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"][
        "properties"
    ] == {"q": {"type": "string"}}
    message = response.choices[0].message
    assert message.content == "calling"
    assert message.tool_calls[0].id == "u1"
    assert json.loads(message.tool_calls[0].function.arguments) == {"q": "x"}
    assert response.choices[0].finish_reason == "tool_calls"
    assert response.usage.prompt_tokens == 7


@pytest.mark.asyncio
async def test_create_without_tools_sends_no_tool_config():
    client = FakeBedrock(
        {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}}
    )
    await ChatCompletions(client).create(
        model="m", messages=[{"role": "user", "content": "hi"}]
    )
    assert "toolConfig" not in client.requests[0]
    with pytest.raises(ValueError):
        await ChatCompletions(client).create(model="m", messages=[], stream=True)
