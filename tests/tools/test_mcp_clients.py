"""MCP client: per-instance sessions, content mapping and failure cleanup."""

import asyncio
import sys
from pathlib import Path

import pytest
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ListToolsResult,
    TextContent,
    TextResourceContents,
    Tool,
)

from app.mcp.server import to_mcp_content
from app.tool import mcp as mcp_tool
from app.tool.base import ToolResult
from app.tool.mcp import MCPClients, _result_to_tool_result


pytestmark = pytest.mark.asyncio

SERVER = str(Path(__file__).with_name("mcp_fixture_server.py"))


async def connect(server_id: str = "fx") -> MCPClients:
    clients = MCPClients()
    await asyncio.wait_for(
        clients.connect_stdio(sys.executable, [SERVER], server_id=server_id), 30
    )
    return clients


async def test_sessions_are_per_instance():
    first, second = await connect(), await connect()
    try:
        assert first.sessions is not second.sessions
        assert first.sessions["fx"] is not second.sessions["fx"]
        assert sorted(first.tool_map) == [
            "mcp_fx_echo",
            "mcp_fx_fail",
            "mcp_fx_picture",
        ]

        await second.disconnect()
        assert second.sessions == {} and second.tool_map == {}

        result = await first.tool_map["mcp_fx_echo"].execute(text="still here")
        assert result.output == "echo: still here"
    finally:
        await first.disconnect()
        await second.disconnect()


async def test_tool_errors_and_images_are_mapped():
    clients = await connect()
    try:
        failed = await clients.tool_map["mcp_fx_fail"].execute()
        assert failed.error and "kaboom" in failed.error

        picture = await clients.tool_map["mcp_fx_picture"].execute()
        assert picture.error is None
        assert picture.output == "a pixel"
        assert picture.base64_image.startswith("iVBOR")
    finally:
        await clients.disconnect()


async def test_failed_connect_leaves_no_state(monkeypatch):
    monkeypatch.setattr(mcp_tool, "CONNECT_TIMEOUT_SECONDS", 2)
    clients = MCPClients()
    with pytest.raises(ConnectionError, match="did not respond"):
        await clients.connect_stdio(sys.executable, ["-c", "import sys; sys.exit(3)"])
    assert clients.sessions == {}
    assert clients.exit_stacks == {}
    assert clients.tool_map == {}


async def test_tools_without_description_are_accepted():
    clients = MCPClients()
    clients._register_tools(
        "srv",
        session=None,
        response=ListToolsResult(
            tools=[Tool(name="bare", description=None, inputSchema={"type": "object"})]
        ),
    )
    assert clients.tool_map["mcp_srv_bare"].description == ""


async def test_result_mapping():
    text = TextContent(type="text", text="hello")
    image = ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")
    resource = EmbeddedResource(
        type="resource",
        resource=TextResourceContents(uri="file:///r.txt", text="from resource"),
    )
    ok = _result_to_tool_result(CallToolResult(content=[text, resource, image]))
    assert ok.output == "hello\nfrom resource"
    assert ok.base64_image == "aGVsbG8="

    error = _result_to_tool_result(CallToolResult(content=[text], isError=True))
    assert error.error == "hello"

    empty = _result_to_tool_result(CallToolResult(content=[]))
    assert empty.output == "No output returned."


async def test_server_returns_text_and_image_content():
    content = to_mcp_content(ToolResult(output="shot", base64_image="iVBORw0KGgo="))
    assert [item.type for item in content] == ["text", "image"]
    assert content[1].mimeType == "image/png"
    with pytest.raises(RuntimeError, match="bad"):
        to_mcp_content(ToolResult(error="bad"))
    assert to_mcp_content({"a": 1}) == '{"a": 1}'
