"""Amazon Bedrock adapter exposing an OpenAI-like ``chat.completions.create`` API.

Requests use the Bedrock Converse API (non-streaming). The synchronous boto3 call runs
in a worker thread so that it never blocks the event loop.
"""

import asyncio
import base64
import binascii
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

import boto3

from app.exceptions import ConfigurationError


_STOP_REASONS = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,(.+)$", re.S)
_ENDPOINT_REGION_RE = re.compile(r"bedrock-runtime[.-]([a-z0-9-]+)\.amazonaws\.com")


class OpenAIResponse:
    """Attribute-access wrapper giving Bedrock responses an OpenAI-like shape."""

    def __init__(self, data):
        # Recursively convert nested dicts and lists to OpenAIResponse objects
        for key, value in data.items():
            if isinstance(value, dict):
                value = OpenAIResponse(value)
            elif isinstance(value, list):
                value = [
                    OpenAIResponse(item) if isinstance(item, dict) else item
                    for item in value
                ]
            setattr(self, key, value)

    def model_dump(self, *args, **kwargs):
        """Return the data as a dict with a ``created_at`` timestamp."""
        return {**self.__dict__, "created_at": datetime.now().isoformat()}


class BedrockClient:
    """Bedrock client; AWS credentials and region come from the usual AWS settings."""

    def __init__(self, region_name: Optional[str] = None):
        try:
            client = (
                boto3.client("bedrock-runtime", region_name=region_name)
                if region_name
                else boto3.client("bedrock-runtime")
            )
        except Exception as e:
            raise ConfigurationError(
                f"Cannot initialise the Amazon Bedrock client: {e}"
            ) from e
        self.client = client
        self.chat = Chat(client)

    @staticmethod
    def region_from_endpoint(base_url: Optional[str]) -> Optional[str]:
        """Extract the region from an endpoint like ``bedrock-runtime.us-west-2.amazonaws.com``."""
        match = _ENDPOINT_REGION_RE.search(base_url or "")
        return match.group(1) if match else None


class Chat:
    def __init__(self, client):
        self.completions = ChatCompletions(client)


def _text_of(content: Any) -> str:
    """Plain text of OpenAI message content (string or list of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else part.get("text") or ""
            for part in content
            if isinstance(part, str) or part.get("type") == "text"
        )
    return ""


def _content_blocks(content: Any) -> List[dict]:
    """Convert OpenAI message content to Bedrock content blocks (text and images)."""
    if isinstance(content, str):
        return [{"text": content}] if content.strip() else []
    blocks: List[dict] = []
    for part in content or []:
        if isinstance(part, str):
            part = {"type": "text", "text": part}
        if part.get("type") == "text" and (part.get("text") or "").strip():
            blocks.append({"text": part["text"]})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            match = _DATA_URL_RE.match(url)
            if not match:
                continue  # Bedrock only accepts inline image bytes
            image_format = "jpeg" if match.group(1) == "jpg" else match.group(1)
            try:
                data = base64.b64decode(match.group(2))
            except (binascii.Error, ValueError):
                continue
            blocks.append(
                {"image": {"format": image_format, "source": {"bytes": data}}}
            )
    return blocks


def _tool_input(arguments: Any) -> dict:
    """Tool call arguments (JSON text) as the object Bedrock expects."""
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"raw": arguments}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class ChatCompletions:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _convert_openai_tools_to_bedrock_format(tools: List[dict]) -> List[dict]:
        bedrock_tools = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            function = tool.get("function", {})
            bedrock_tools.append(
                {
                    "toolSpec": {
                        "name": function.get("name", ""),
                        "description": function.get("description") or "",
                        "inputSchema": {
                            "json": function.get("parameters")
                            or {"type": "object", "properties": {}}
                        },
                    }
                }
            )
        return bedrock_tools

    def _tool_config(
        self, tools: Optional[List[dict]], tool_choice: str
    ) -> Optional[dict]:
        if not tools or tool_choice == "none":
            return None
        bedrock_tools = self._convert_openai_tools_to_bedrock_format(tools)
        if not bedrock_tools:
            return None
        choice = {"any": {}} if tool_choice == "required" else {"auto": {}}
        return {"tools": bedrock_tools, "toolChoice": choice}

    @staticmethod
    def _convert_openai_messages_to_bedrock_format(
        messages: List[dict],
    ) -> Tuple[List[dict], List[dict]]:
        """Convert OpenAI messages to Bedrock (system blocks, messages).

        Consecutive messages of the same Bedrock role are merged, which groups the
        results of parallel tool calls into a single user turn as Bedrock requires.
        """
        system_blocks: List[dict] = []
        bedrock_messages: List[dict] = []

        def append(role: str, blocks: List[dict]) -> None:
            if not blocks:
                return
            if bedrock_messages and bedrock_messages[-1]["role"] == role:
                bedrock_messages[-1]["content"].extend(blocks)
            else:
                bedrock_messages.append({"role": role, "content": blocks})

        for message in messages:
            role = message.get("role")
            if role == "system":
                text = _text_of(message.get("content"))
                if text.strip():
                    system_blocks.append({"text": text})
            elif role == "user":
                append("user", _content_blocks(message.get("content")))
            elif role == "assistant":
                blocks = _content_blocks(message.get("content"))
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": call["id"],
                                "name": function.get("name", ""),
                                "input": _tool_input(function.get("arguments")),
                            }
                        }
                    )
                append("assistant", blocks)
            elif role == "tool":
                text = _text_of(message.get("content")) or "(no output)"
                append(
                    "user",
                    [
                        {
                            "toolResult": {
                                "toolUseId": message.get("tool_call_id"),
                                "content": [{"text": text}],
                            }
                        }
                    ],
                )
            else:
                raise ValueError(f"Invalid role: {role}")
        return system_blocks, bedrock_messages

    @staticmethod
    def _convert_bedrock_response_to_openai_format(
        bedrock_response: dict,
    ) -> OpenAIResponse:
        message = bedrock_response.get("output", {}).get("message", {})
        blocks = message.get("content") or []
        content = "".join(block.get("text", "") for block in blocks)
        tool_calls = [
            {
                "id": block["toolUse"]["toolUseId"],
                "type": "function",
                "function": {
                    "name": block["toolUse"]["name"],
                    "arguments": json.dumps(
                        block["toolUse"].get("input", {}), ensure_ascii=False
                    ),
                },
            }
            for block in blocks
            if block.get("toolUse")
        ]
        stop_reason = bedrock_response.get("stopReason", "end_turn")
        usage = bedrock_response.get("usage", {})
        return OpenAIResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4()}",
                "created": int(time.time()),
                "object": "chat.completion",
                "system_fingerprint": None,
                "choices": [
                    {
                        "finish_reason": _STOP_REASONS.get(stop_reason, stop_reason),
                        "index": 0,
                        "message": {
                            "content": content or None,
                            "role": message.get("role", "assistant"),
                            "tool_calls": tool_calls or None,
                            "function_call": None,
                        },
                    }
                ],
                "usage": {
                    "completion_tokens": usage.get("outputTokens", 0),
                    "prompt_tokens": usage.get("inputTokens", 0),
                    "total_tokens": usage.get("totalTokens", 0),
                },
            }
        )

    async def create(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
        stream: bool = False,
        tools: Optional[List[dict]] = None,
        tool_choice: Literal["none", "auto", "required"] = "auto",
        **kwargs,
    ) -> OpenAIResponse:
        """Run a Converse request; extra OpenAI arguments (e.g. timeout) are ignored."""
        if stream:
            raise ValueError("Streaming is not supported by the Bedrock adapter")
        (
            system_blocks,
            bedrock_messages,
        ) = self._convert_openai_messages_to_bedrock_format(messages)
        inference_config: Dict[str, Any] = {"maxTokens": max_tokens}
        if temperature is not None:
            inference_config["temperature"] = temperature
        request: Dict[str, Any] = {
            "modelId": model,
            "messages": bedrock_messages,
            "inferenceConfig": inference_config,
        }
        if system_blocks:
            request["system"] = system_blocks
        tool_config = self._tool_config(tools, tool_choice)
        if tool_config:
            request["toolConfig"] = tool_config

        response = await asyncio.to_thread(self.client.converse, **request)
        return self._convert_bedrock_response_to_openai_format(response)
