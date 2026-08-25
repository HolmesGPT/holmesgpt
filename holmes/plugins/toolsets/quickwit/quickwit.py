"""Quickwit pod-logging toolset implementing the unified fetch_pod_logs API.

The model never writes a Quickwit query: it supplies typed parameters (namespace, pod_name,
filter, time range) and this toolset builds the query deterministically — exact field terms
only (no wildcards, which Quickwit silently ignores), identifiers sanitized so quoting can
never corrupt the query, sidecar noise with empty messages dropped before the limit is
applied, and regex filtering done in code rather than pushed to the query language.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Tuple, Type

import requests  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from holmes.utils.pydantic_utils import ToolsetConfig

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_LOG_LIMIT,
    DEFAULT_TIME_SPAN_SECONDS,
    FetchPodLogsParams,
    PodLoggingTool,
)
from holmes.plugins.toolsets.utils import process_timestamps_to_int

# fetch generously before code-side filtering/dropping so high-volume sidecars can't
# starve the interesting container's lines out of the window
FETCH_HITS = 1000

# k8s object names are DNS-1123 (alphanumerics, '-', '.'); everything else is stripped so a
# crafted identifier can never alter the query structure
_IDENTIFIER_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


class QuickwitConfig(ToolsetConfig):
    """Configuration for Quickwit API access.

    Example configuration:
    ```yaml
    api_url: "http://quickwit.monitoring.svc:7280"
    index: "k8s-logs"
    ```
    """

    api_url: str = Field(
        title="API URL",
        description="Base URL of the Quickwit server (without trailing slash).",
        examples=["http://quickwit.monitoring.svc:7280"],
    )
    index: str = Field(
        title="Index ID",
        description="Quickwit index holding the Kubernetes logs.",
        examples=["k8s-logs"],
    )
    timestamp_field: str = Field(
        default="timestamp",
        title="Timestamp Field",
        description="Document field holding the event time (unix seconds).",
    )
    message_field: str = Field(
        default="message",
        title="Message Field",
        description="Document field holding the log line text (dotted paths supported).",
    )
    namespace_field: str = Field(
        default="kubernetes.pod_namespace",
        title="Namespace Field",
        description="Document field holding the pod namespace (dotted paths supported).",
    )
    pod_field: str = Field(
        default="kubernetes.pod_name",
        title="Pod Field",
        description="Document field holding the pod name (dotted paths supported).",
    )
    container_field: str = Field(
        default="kubernetes.container_name",
        title="Container Field",
        description="Document field holding the container name (dotted paths supported).",
    )
    timeout_seconds: int = Field(
        default=30,
        title="Timeout Seconds",
        description="Request timeout in seconds.",
    )


def _sanitize(value: str) -> str:
    return _IDENTIFIER_SAFE.sub("", value or "")


def _dotted_get(obj: Dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _compile_or_literal(pattern: str) -> re.Pattern:
    """Filters are advertised as regex; a malformed pattern degrades to a literal match."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


class QuickwitLogsToolset(Toolset):
    """Fetch pod logs from a Quickwit index via the unified fetch_pod_logs API."""

    config_classes: ClassVar[list[Type[BaseModel]]] = [QuickwitConfig]

    def __init__(self):
        super().__init__(
            name="quickwit/logs",
            description="Read Kubernetes pod logs stored in Quickwit using a unified API",
            docs_url="https://quickwit.io/docs/reference/rest-api",
            icon_url="https://avatars.githubusercontent.com/u/74226539?s=200&v=4",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [PodLoggingTool(self)]

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "quickwit/logs requires config with api_url and index"
        try:
            self.config = QuickwitConfig(**config)
        except Exception as e:
            return False, f"Failed to validate Quickwit configuration: {e}"
        return self._health_check()

    @property
    def quickwit_config(self) -> QuickwitConfig:
        return self.config  # type: ignore[return-value]

    def _health_check(self) -> Tuple[bool, str]:
        cfg = self.quickwit_config
        url = f"{cfg.api_url.rstrip('/')}/health/livez"
        try:
            response = requests.get(url, timeout=min(cfg.timeout_seconds, 10))
            response.raise_for_status()
            return True, f"Connected to Quickwit at {cfg.api_url}"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:1000] if e.response is not None else ""
            return False, (
                f"Quickwit health check failed for {url}: HTTP {status}. Response: {body}"
            )
        except Exception as e:
            return False, f"Quickwit health check failed for {url}: {e}"

    def fetch_pod_logs(self, params: FetchPodLogsParams) -> StructuredToolResult:
        cfg = self.quickwit_config
        start_unix, end_unix = process_timestamps_to_int(
            start=params.start_time,
            end=params.end_time,
            default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
        )
        limit = params.limit or DEFAULT_LOG_LIMIT

        namespace = _sanitize(params.namespace)
        pod_name = _sanitize(params.pod_name)
        query = f"{cfg.namespace_field}:{namespace} AND {cfg.pod_field}:{pod_name}"

        request_params: Dict[str, Any] = {
            "query": query,
            "max_hits": FETCH_HITS,
            # newest-first so a large result set can never rotate the recent lines out
            "sort_by": f"-{cfg.timestamp_field}",
        }
        request_params["start_timestamp"] = start_unix
        request_params["end_timestamp"] = end_unix

        url = f"{cfg.api_url.rstrip('/')}/api/v1/{cfg.index}/search"
        try:
            response = requests.get(
                url, params=request_params, timeout=cfg.timeout_seconds
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:1000] if e.response is not None else ""
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Quickwit search failed for {url} with HTTP {status} "
                    f"(params={json.dumps(request_params, default=str)}). "
                    f"Response: {body}"
                ),
                params=params.model_dump(),
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Quickwit search failed for {url} "
                    f"(params={json.dumps(request_params, default=str)}): {e}"
                ),
                params=params.model_dump(),
            )

        if not isinstance(payload, dict):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Quickwit search for {url} returned an unexpected response shape "
                    f"(expected a JSON object): {str(payload)[:500]}"
                ),
                params=params.model_dump(),
            )

        include = _compile_or_literal(params.filter) if params.filter else None
        exclude = (
            _compile_or_literal(params.exclude_filter)
            if params.exclude_filter
            else None
        )

        rows = []
        for hit in payload.get("hits") or []:
            message = _dotted_get(hit, cfg.message_field)
            if not message:
                continue  # high-volume sidecars whose lines carry no message are pure noise
            if include and not include.search(message):
                continue
            if exclude and exclude.search(message):
                continue
            rows.append(
                (
                    _dotted_get(hit, cfg.timestamp_field) or 0,
                    _dotted_get(hit, cfg.container_field) or "?",
                    message,
                )
            )

        rows.sort(key=lambda r: r[0])
        total_matched = len(rows)
        rows = rows[-limit:]  # keep the most recent lines, chronological order

        if not rows:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data=(
                    f"No logs found for pod {params.pod_name} in namespace {params.namespace} "
                    f"between {start_unix} and {end_unix} (unix seconds)"
                    + (f" matching filter '{params.filter}'" if params.filter else "")
                    + f" (query: {query})."
                ),
                params=params.model_dump(),
            )

        lines = []
        if total_matched > len(rows):
            lines.append(
                f"[showing the {len(rows)} most recent of {total_matched} matching lines]"
            )
        for ts, container, message in rows:
            iso = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                if isinstance(ts, (int, float)) and ts
                else "?"
            )
            lines.append(f"{iso} {container} {message}")

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="\n".join(lines),
            params=params.model_dump(),
        )
