"""Minimal stdio MCP server used by the MCP client tests."""

import base64

from mcp.server.fastmcp import FastMCP, Image


# 1x1 transparent PNG
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

server = FastMCP("fixture")


@server.tool()
def echo(text: str) -> str:
    """Return the given text."""
    return f"echo: {text}"


@server.tool()
def picture() -> list:
    """Return a caption and an image."""
    return ["a pixel", Image(data=PIXEL_PNG, format="png")]


@server.tool()
def fail() -> str:
    """Always fails."""
    raise ValueError("kaboom")


if __name__ == "__main__":
    server.run(transport="stdio")
