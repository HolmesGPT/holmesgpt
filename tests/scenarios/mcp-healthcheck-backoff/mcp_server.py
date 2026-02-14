"""Simple MCP server (SSE) with a configurable startup delay.

Used by the scenario test to simulate a slow-starting MCP server that
isn't ready when Holmes performs its initial healthcheck.
"""

import argparse
import time

from mcp.server.fastmcp import FastMCP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=int, default=20, help="Seconds to wait before starting")
    parser.add_argument("--port", type=int, default=9123, help="Port to listen on")
    args = parser.parse_args()

    if args.delay > 0:
        print(f"MCP server delaying startup by {args.delay}s...", flush=True)
        time.sleep(args.delay)

    mcp = FastMCP("delayed-test-server", port=args.port)

    @mcp.tool()
    def ping() -> str:
        """Simple health-check tool."""
        return "pong"

    print(f"MCP server starting on port {args.port}", flush=True)
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
