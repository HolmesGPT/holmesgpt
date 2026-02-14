"""Delayed MCP SSE server for end-to-end testing.

Starts listening on the given port but waits DELAY seconds before
registering the MCP routes. During the delay window, connections get
refused (or reset), which lets us verify Holmes's retry behaviour.

Usage:
    python tests/delayed_mcp_server.py --port 9199 --delay 12
"""

import argparse
import sys
import time
import threading

from mcp.server.fastmcp import FastMCP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9199)
    parser.add_argument("--delay", type=int, default=10)
    args = parser.parse_args()

    print(f"[delayed-mcp] delay={args.delay}s port={args.port}", file=sys.stderr, flush=True)
    time.sleep(args.delay)
    print(f"[delayed-mcp] starting SSE server on port {args.port}", file=sys.stderr, flush=True)

    mcp = FastMCP("delayed-test-server", port=args.port)

    @mcp.tool()
    def ping() -> str:
        """Simple health-check tool."""
        return "pong"

    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
