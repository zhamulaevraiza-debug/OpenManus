import asyncio
import re
from contextlib import AsyncExitStack
from typing import Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ListToolsResult,
    TextContent,
    TextResourceContents,
)

from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


CALL_TIMEOUT_SECONDS = 300
# A server that dies during start-up never answers ``initialize``.
CONNECT_TIMEOUT_SECONDS = 30


def _result_to_tool_result(result: CallToolResult) -> ToolResult:
    """Map MCP content (text, images, embedded resources) and isError to a ToolResult."""
    texts: List[str] = []
    image: Optional[str] = None
    for item in result.content:
        if isinstance(item, TextContent):
            texts.append(item.text)
        elif isinstance(item, ImageContent):
            if image is None:
                image = item.data
            else:
                texts.append(f"[additional image omitted ({item.mimeType})]")
        elif isinstance(item, EmbeddedResource):
            resource = item.resource
            if isinstance(resource, TextResourceContents):
                texts.append(resource.text)
            else:
                texts.append(
                    f"[binary resource {resource.uri} ({resource.mimeType or 'unknown type'})]"
                )
    text = "\n".join(texts)
    if result.isError:
        return ToolResult(
            error=text or "The MCP tool reported an error", base64_image=image
        )
    if not text and image is None:
        text = "No output returned."
    return ToolResult(output=text, base64_image=image)


class MCPClientTool(BaseTool):
    """Represents a tool proxy that can be called on the MCP server from the client side."""

    session: Optional[ClientSession] = None
    server_id: str = ""  # Add server identifier
    original_name: str = ""

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool by making a remote call to the MCP server."""
        if not self.session:
            return ToolResult(error="Not connected to MCP server")

        try:
            logger.info(f"Executing tool: {self.original_name}")
            result = await asyncio.wait_for(
                self.session.call_tool(self.original_name, kwargs),
                CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                error=f"MCP tool {self.original_name} timed out after {CALL_TIMEOUT_SECONDS}s"
            )
        except Exception as e:
            return ToolResult(error=f"Error executing tool: {str(e)}")
        return _result_to_tool_result(result)


class MCPClients(ToolCollection):
    """
    A collection of tools that connects to multiple MCP servers and manages available tools through the Model Context Protocol.

    Sessions belong to this instance: connect, use and disconnect it from the same
    asyncio task (the transports use task-bound cancel scopes).
    """

    description: str = "MCP client tools for server interaction"

    def __init__(self):
        super().__init__()  # Initialize with empty tools list
        self.name = "mcp"  # Keep name for backward compatibility
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stacks: Dict[str, AsyncExitStack] = {}

    async def connect_sse(self, server_url: str, server_id: str = "") -> None:
        """Connect to an MCP server using SSE transport."""
        if not server_url:
            raise ValueError("Server URL is required.")

        server_id = server_id or server_url
        await self._connect(server_id, sse_client(url=server_url))

    async def connect_stdio(
        self, command: str, args: List[str], server_id: str = ""
    ) -> None:
        """Connect to an MCP server using stdio transport."""
        if not command:
            raise ValueError("Server command is required.")

        server_id = server_id or command
        server_params = StdioServerParameters(command=command, args=args)
        await self._connect(server_id, stdio_client(server_params))

    async def _connect(self, server_id: str, transport) -> None:
        """Open ``transport``, initialize the session and register its tools.

        On any failure the partially opened transport is closed and no state is kept.
        """
        # Always ensure clean disconnection before new connection
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        try:
            read, write = await exit_stack.enter_async_context(transport)
            session = await exit_stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT_SECONDS)
            response = await asyncio.wait_for(
                session.list_tools(), CONNECT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            await self._close_stack(server_id, exit_stack)
            raise ConnectionError(
                f"MCP server {server_id} did not respond within "
                f"{CONNECT_TIMEOUT_SECONDS}s"
            ) from None
        except BaseException:
            await self._close_stack(server_id, exit_stack)
            raise

        self.sessions[server_id] = session
        self.exit_stacks[server_id] = exit_stack
        self._register_tools(server_id, session, response)

    def _register_tools(
        self, server_id: str, session: ClientSession, response: ListToolsResult
    ) -> None:
        """Create proxy tools for every tool exposed by the server."""
        for tool in response.tools:
            original_name = tool.name
            tool_name = self._sanitize_tool_name(f"mcp_{server_id}_{original_name}")

            server_tool = MCPClientTool(
                name=tool_name,
                description=tool.description or "",
                parameters=tool.inputSchema,
                session=session,
                server_id=server_id,
                original_name=original_name,
            )
            self.tool_map[tool_name] = server_tool

        # Update tools tuple
        self.tools = tuple(self.tool_map.values())
        logger.info(
            f"Connected to server {server_id} with tools: {[tool.name for tool in response.tools]}"
        )

    def _sanitize_tool_name(self, name: str) -> str:
        """Sanitize tool name to match MCPClientTool requirements."""
        # Replace invalid characters with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

        # Remove consecutive underscores
        sanitized = re.sub(r"_+", "_", sanitized)

        # Remove leading/trailing underscores
        sanitized = sanitized.strip("_")

        # Truncate to 64 characters if needed
        return sanitized[:64]

    async def list_tools(self) -> ListToolsResult:
        """List all available tools."""
        tools_result = ListToolsResult(tools=[])
        for session in self.sessions.values():
            response = await session.list_tools()
            tools_result.tools += response.tools
        return tools_result

    @staticmethod
    async def _close_stack(server_id: str, exit_stack: AsyncExitStack) -> None:
        """Close a server's transport; errors (e.g. closing from another task) are logged."""
        try:
            await exit_stack.aclose()
        except Exception as e:
            logger.warning(f"Error closing MCP transport for {server_id}: {e}")

    async def disconnect(self, server_id: str = "") -> None:
        """Disconnect from a specific MCP server or all servers if no server_id provided."""
        if server_id:
            if server_id in self.sessions:
                try:
                    exit_stack = self.exit_stacks.get(server_id)

                    # Close the exit stack which will handle session cleanup
                    if exit_stack:
                        await self._close_stack(server_id, exit_stack)

                    # Clean up references
                    self.sessions.pop(server_id, None)
                    self.exit_stacks.pop(server_id, None)

                    # Remove tools associated with this server
                    self.tool_map = {
                        k: v
                        for k, v in self.tool_map.items()
                        if v.server_id != server_id
                    }
                    self.tools = tuple(self.tool_map.values())
                    logger.info(f"Disconnected from MCP server {server_id}")
                except Exception as e:
                    logger.error(f"Error disconnecting from server {server_id}: {e}")
        else:
            # Disconnect from all servers in a deterministic order
            for sid in sorted(list(self.sessions.keys())):
                await self.disconnect(sid)
            self.tool_map = {}
            self.tools = tuple()
            logger.info("Disconnected from all MCP servers")
