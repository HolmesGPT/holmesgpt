"""Local MCP fixture for contract tests and a live-model walkthrough.

This server returns simulated observations, never production telemetry.
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field


server = FastMCP("service-observability-fixture")


def check_service(service: str) -> None:
    if service != "checkout":
        raise ValueError(f"No fixture for service {service!r}; available service: checkout")


@server.tool()
def service_logs(
    service: str,
    minutes: Annotated[int, Field(ge=1, le=60)] = 15,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Query bounded application error logs for a service (simulated data)."""
    check_service(service)
    return {"simulated": True, "service": service, "minutes": minutes, "limit": limit,
            "logs": [{"timestamp": "2026-09-28T08:05:00Z", "trace_id": "checkout-demo-001",
                      "message": "SQLTransientConnectionException: Connection is not available, request timed out after 3000ms"}][:limit]}


@server.tool()
def service_metrics(service: str, minutes: Annotated[int, Field(ge=1, le=60)] = 15) -> dict:
    """Query latency, CPU and connection-pool metrics (simulated data)."""
    check_service(service)
    return {"simulated": True, "service": service, "minutes": minutes,
            "observed_at": "2026-09-28T08:05:00Z", "p99_ms": 3100, "cpu_ratio": 0.19,
            "db_pool_active": 10, "db_pool_max": 10, "db_pool_pending": 47}


@server.tool()
def service_config(service: str) -> dict:
    """Read current and previous non-secret service settings (simulated data)."""
    check_service(service)
    return {"simulated": True, "service": service, "release": "r42",
            "db_pool_max": 10, "previous_db_pool_max": 50,
            "changed_at": "2026-09-28T08:00:00Z"}


@server.tool()
def service_instances(service: str) -> dict:
    """Read service instance readiness and restart counts (simulated data)."""
    check_service(service)
    return {"simulated": True, "service": service, "ready": 3, "desired": 3,
            "restarts": 0, "release": "r42"}


if __name__ == "__main__":
    server.run(transport="stdio")
