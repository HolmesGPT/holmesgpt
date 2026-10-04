"""Local MCP fixture. All observations are explicitly simulated."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from tracepilot.config import ScopeBinding
from tracepilot.models import Candidate
from tracepilot.scenarios import ScenarioTools, demo_scope


server = FastMCP("TracePilot Simulated Observability")
gateway = ScenarioTools("db-pool")


def query(tool: str, service: str, namespace: str, start: str, end: str, pod: str | None = None) -> dict[str, Any]:
    args = {"service": service, "namespace": namespace, "start": start, "end": end}
    if pod:
        args["pod"] = pod
    ScopeBinding().validate_arguments(args, demo_scope())
    return gateway.execute(Candidate(id=tool, tool=tool, arguments=args, purpose="Fixture query", direction="fixture")).data


@server.tool()
def service_instances(service: str, namespace: str, start: str, end: str) -> dict[str, Any]:
    """Read simulated instance status."""
    return query("service_instances", service, namespace, start, end)


@server.tool()
def service_traces(service: str, namespace: str, start: str, end: str) -> dict[str, Any]:
    """Read simulated slow spans and affected instance."""
    return query("service_traces", service, namespace, start, end)


@server.tool()
def service_logs(service: str, namespace: str, start: str, end: str, pod: str | None = None) -> dict[str, Any]:
    """Read simulated application logs."""
    return query("service_logs", service, namespace, start, end, pod)


@server.tool()
def service_metrics(service: str, namespace: str, start: str, end: str, pod: str | None = None) -> dict[str, Any]:
    """Read simulated CPU and connection-pool metrics."""
    return query("service_metrics", service, namespace, start, end, pod)


@server.tool()
def service_config(service: str, namespace: str, start: str, end: str) -> dict[str, Any]:
    """Read simulated non-secret configuration changes."""
    return query("service_config", service, namespace, start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    server.settings.host = "127.0.0.1"
    server.settings.port = args.port
    server.settings.stateless_http = True
    server.run(transport=args.transport)
