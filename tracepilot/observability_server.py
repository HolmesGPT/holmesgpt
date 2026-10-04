"""Read-only MCP tools for configured Prometheus, Loki, Tempo and Kubernetes APIs."""

import json
import os
import re
from typing import Annotated, Any
from urllib.parse import quote

import requests
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from tracepilot.config import validate_endpoint
from tracepilot.models import InvestigationScope, utc_now


class ObservabilityClient:
    def __init__(self, environment: dict[str, str]):
        self.env = environment

    def get(self, backend: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        base = self.env.get(f"{backend}_URL", "")
        validate_endpoint(base)
        headers = {"Accept": "application/json"}
        if self.env.get(f"{backend}_TOKEN"):
            headers["Authorization"] = "Bearer " + self.env[f"{backend}_TOKEN"]
        if self.env.get(f"{backend}_TENANT"):
            headers["X-Scope-OrgID"] = self.env[f"{backend}_TENANT"]
        with requests.get(base.rstrip("/") + path, params=params, headers=headers, timeout=(3, 12),
                          verify=self.env.get(f"{backend}_CA_BUNDLE") or True, allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"{backend} returned unexpected HTTP {response.status_code}")
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError(f"{backend} response exceeds 2 MB; narrow the query")
                chunks.append(chunk)
            data = json.loads(b"".join(chunks))
        if not isinstance(data, dict):
            raise ValueError(f"{backend} returned an invalid JSON object")
        return data

    def labels(self, service: str, namespace: str, pod: str | None = None) -> str:
        names = [self.env.get("OBS_SERVICE_LABEL", "service"), self.env.get("OBS_NAMESPACE_LABEL", "namespace")]
        if pod:
            names.append(self.env.get("OBS_POD_LABEL", "pod"))
        if len(set(names)) != len(names):
            raise ValueError("Configured telemetry label names must be distinct")
        fields = dict(zip(names, [service, namespace, *([pod] if pod else [])]))
        if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name) for name in names):
            raise ValueError("Invalid configured telemetry label name")
        return "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in fields.items()) + "}"


def scope_for(service: str, namespace: str, start: str, end: str) -> InvestigationScope:
    return InvestigationScope(service=service, namespace=namespace, start=start, end=end)


def build_server(environment: dict[str, str] | None = None) -> FastMCP:
    env = dict(os.environ) if environment is None else environment
    client = ObservabilityClient(env)
    server = FastMCP("TracePilot Observability")

    if env.get("LOKI_URL"):
        @server.tool()
        def service_logs(service: str, namespace: str, start: str, end: str, pod: str | None = None,
                         contains: str = "", limit: Annotated[int, Field(ge=1, le=100)] = 50) -> dict[str, Any]:
            """Read scoped Loki logs. contains is a literal substring, never a LogQL program."""
            scope = scope_for(service, namespace, start, end)
            query = client.labels(service, namespace, pod)
            if contains:
                query += " |= " + json.dumps(contains)
            result = client.get("LOKI", "/loki/api/v1/query_range", {
                "query": query, "start": scope.start.isoformat(), "end": scope.end.isoformat(), "limit": limit, "direction": "forward"})
            if result.get("status") != "success":
                raise ValueError("Loki query did not succeed")
            return {"source": "loki", "scope": scope.model_dump(mode="json"), "query": query, "result": result}

    if env.get("PROMETHEUS_URL"):
        @server.tool()
        def service_metrics(service: str, namespace: str, start: str, end: str,
                            metric: Annotated[str, Field(pattern=r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")], pod: str | None = None,
                            step_seconds: Annotated[int, Field(ge=15, le=3600)] = 60) -> dict[str, Any]:
            """Read a named raw metric with mandatory service/namespace labels; max 100 series."""
            scope = scope_for(service, namespace, start, end)
            if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", metric):
                raise ValueError("metric must be a name, not a PromQL expression")
            if (scope.end - scope.start).total_seconds() / step_seconds > 1440:
                raise ValueError("Too many time points; increase step_seconds")
            query = metric + client.labels(service, namespace, pod)
            result = client.get("PROMETHEUS", "/api/v1/query_range", {"query": query, "start": scope.start.isoformat(),
                                "end": scope.end.isoformat(), "step": step_seconds, "limit": 100, "timeout": "10s"})
            if result.get("status") != "success":
                raise ValueError("Prometheus query did not succeed")
            return {"source": "prometheus", "scope": scope.model_dump(mode="json"), "query": query, "result": result}

    if env.get("TEMPO_URL"):
        @server.tool()
        def service_traces(service: str, namespace: str, start: str, end: str,
                           limit: Annotated[int, Field(ge=1, le=50)] = 20) -> dict[str, Any]:
            """Search traces by resource.service.name and resource.k8s.namespace.name."""
            scope = scope_for(service, namespace, start, end)
            query = "{ resource.service.name = " + json.dumps(service) + " && resource.k8s.namespace.name = " + json.dumps(namespace) + " }"
            result = client.get("TEMPO", "/api/search", {"q": query, "start": int(scope.start.timestamp()),
                                                       "end": int(scope.end.timestamp()), "limit": limit})
            return {"source": "tempo", "scope": scope.model_dump(mode="json"), "query": query, "result": result}

    if env.get("KUBERNETES_URL"):
        @server.tool()
        def service_instances(service: str, namespace: str, start: str, end: str) -> dict[str, Any]:
            """Read CURRENT pod status; this snapshot cannot establish historical incident state."""
            scope = scope_for(service, namespace, start, end)
            if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", namespace):
                raise ValueError("Invalid Kubernetes namespace")
            if not re.fullmatch(r"[A-Za-z0-9]([-_.A-Za-z0-9]*[A-Za-z0-9])?", service):
                raise ValueError("Invalid service label value")
            result = client.get("KUBERNETES", f"/api/v1/namespaces/{quote(namespace, safe='')}/pods",
                                {"labelSelector": "app.kubernetes.io/name=" + service, "limit": 100})
            pods = [{"name": item.get("metadata", {}).get("name"), "status": item.get("status", {})} for item in result.get("items", [])]
            return {"source": "kubernetes", "scope": scope.model_dump(mode="json"), "pods": pods,
                    "collected_at": utc_now().isoformat(), "temporal_semantics": "current_snapshot",
                    "truncated": bool(result.get("metadata", {}).get("continue"))}
    return server


if __name__ == "__main__":
    build_server().run(transport="stdio")
