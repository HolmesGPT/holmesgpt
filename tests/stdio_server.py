# stdio_server.py

import base64

from mcp.server.fastmcp import FastMCP, Image

# Create the MCP server
mcp = FastMCP("STDIO Example Server")


@mcp.tool()
def greet(name: str) -> str:
    """Greet a user by name"""
    return f"Hello, {name}! Welcome to the STDIO server."


@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two numbers and return the result"""
    return f"The sum of {a} and {b} is {a + b}."


@mcp.tool()
def get_test_image() -> Image:
    """Return a tiny 1x1 red PNG image for testing MCP image passthrough"""
    # Minimal valid 1x1 red PNG (67 bytes)
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg=="
    )
    return Image(data=png_bytes, format="png")


if __name__ == "__main__":
    print("Starting MCP server with STDIO transport...", file=__import__("sys").stderr)
    # The run() method uses stdio by default
    mcp.run()
