"""Holmes's own pods and logs, for the platform's agent health / logs view.

Served as the ``holmes_logs`` internal request kind of the remote tool-call
worker (never as an LLM tool). Reads Holmes's own pods and logs from the
Kubernetes API server; if Holmes may not read them, the result carries the error.

The result is bounded: ``tail_lines`` is clamped to 1..HOLMES_LOGS_MAX_TAIL_LINES
and the whole result to REMOTE_TOOL_RESULT_MAX_BYTES once JSON-encoded, keeping
the newest lines of each stream.
"""

import json
import os
import socket
from typing import Any, Callable, Dict, List, Optional, Tuple

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, ConfigDict, field_validator

from holmes.common.env_vars import (
    HOLMES_LOGS_DEFAULT_TAIL_LINES,
    HOLMES_LOGS_MAX_TAIL_LINES,
    REMOTE_TOOL_RESULT_MAX_BYTES,
)
from holmes.utils.holmes_status import _detect_runner_namespace

# Holmes runs a handful of replicas; this only guards against a selector that
# unexpectedly matches much more than Holmes.
MAX_PODS = 20
LOG_REQUEST_TIMEOUT_SECONDS = 20



class HolmesLogsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tail_lines: int = HOLMES_LOGS_DEFAULT_TAIL_LINES
    previous: bool = False
    pod_name: Optional[str] = None
    include_logs: bool = True

    @field_validator("tail_lines", mode="before")
    @classmethod
    def _clamp_tail_lines(cls, value: Any) -> int:
        if value is None:
            return HOLMES_LOGS_DEFAULT_TAIL_LINES
        return max(1, min(HOLMES_LOGS_MAX_TAIL_LINES, int(value)))


# ---- environment ----


def detect_pod_name() -> str:
    # Kubernetes sets HOSTNAME to the pod name (absent a custom spec.hostname).
    return os.environ.get("HOSTNAME") or socket.gethostname()


def _default_core_api() -> Any:
    k8s_config.load_incluster_config()
    return k8s_client.CoreV1Api()


def label_selector_for(labels: Dict[str, str]) -> Optional[str]:
    """Selector matching this pod's siblings, from its own labels.

    Prefers ``app.kubernetes.io/name`` + ``instance`` (release-scoped), else
    ``app``. None means no usable label: only the pod itself is reported.
    """
    name = labels.get("app.kubernetes.io/name")
    instance = labels.get("app.kubernetes.io/instance")
    if name and instance:
        return f"app.kubernetes.io/name={name},app.kubernetes.io/instance={instance}"
    app = labels.get("app")
    if app:
        return f"app={app}"
    return None


def _describe_api_error(action: str, e: Exception) -> str:
    if isinstance(e, ApiException):
        reason = (e.reason or "").strip()
        return f"{action} failed: HTTP {e.status} {reason}".rstrip()
    return f"{action} failed: {type(e).__name__}: {e}"


# ---- pod summaries ----


def _state_of(state: Any) -> Dict[str, Optional[str]]:
    if state is None:
        return {"state": "unknown", "reason": None}
    if getattr(state, "running", None):
        return {"state": "running", "reason": None}
    waiting = getattr(state, "waiting", None)
    if waiting:
        return {"state": "waiting", "reason": getattr(waiting, "reason", None)}
    terminated = getattr(state, "terminated", None)
    if terminated:
        return {"state": "terminated", "reason": getattr(terminated, "reason", None)}
    return {"state": "unknown", "reason": None}


def _summarize_pod(pod: Any) -> Dict[str, Any]:
    status = pod.status
    spec_containers = list(getattr(pod.spec, "containers", None) or [])
    statuses = {
        cs.name: cs for cs in (getattr(status, "container_statuses", None) or [])
    }
    containers = []
    for c in spec_containers:
        cs = statuses.get(c.name)
        current = _state_of(getattr(cs, "state", None))
        last = _state_of(getattr(cs, "last_state", None))
        containers.append(
            {
                "name": c.name,
                "ready": bool(getattr(cs, "ready", False)),
                "restart_count": int(getattr(cs, "restart_count", 0) or 0),
                "state": current["state"],
                "state_reason": current["reason"],
                "last_state_reason": last["reason"],
            }
        )
    ready = False
    for cond in getattr(status, "conditions", None) or []:
        if cond.type == "Ready":
            ready = cond.status == "True"
    start_time = getattr(status, "start_time", None)
    return {
        "name": pod.metadata.name,
        "phase": getattr(status, "phase", None),
        "ready": ready,
        "restarts": sum(c["restart_count"] for c in containers),
        "start_time": start_time.isoformat() if start_time else None,
        "containers": containers,
    }


# ---- log bounding ----


def _keep_tail(text: str, max_chars: int) -> Tuple[str, bool]:
    """Keep the newest ``max_chars`` of ``text``, cut at a line boundary."""
    if len(text) <= max_chars:
        return text, False
    cut = text[len(text) - max_chars :]
    newline = cut.find("\n")
    if 0 <= newline < len(cut) - 1:
        cut = cut[newline + 1 :]
    return cut, True


def _bound_logs(entries: List[Dict[str, Any]], budget: int) -> None:
    """Share ``budget`` chars across entries; unused share rolls forward."""
    remaining = budget
    for i, entry in enumerate(entries):
        share = remaining // (len(entries) - i)
        text, cut = _keep_tail(entry["text"], share)
        entry["text"] = text
        entry["truncated"] = entry["truncated"] or cut
        remaining -= len(text)


def _encoded_size(result: Dict[str, Any]) -> int:
    # The same encoding the tool-call worker sends.
    return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))


def _result(
    namespace: Optional[str],
    pods: List[Dict[str, Any]],
    logs: List[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    max_bytes = REMOTE_TOOL_RESULT_MAX_BYTES
    result = {"namespace": namespace, "pods": pods, "logs": logs, "error": error}
    # Escaping and multi-byte characters make the encoded size larger than the
    # character count, so shrink the character budget until the result fits.
    budget = max_bytes
    while logs:
        _bound_logs(logs, budget)
        size = _encoded_size(result)
        if size <= max_bytes or budget == 0:
            break
        budget = int(budget * max_bytes / size * 0.95)
    return result


# ---- entry point ----


def get_holmes_logs(
    request: HolmesLogsRequest,
    core_api_factory: Callable[[], Any] = _default_core_api,
) -> Dict[str, Any]:
    namespace = _detect_runner_namespace()
    own_pod_name = detect_pod_name()

    if not namespace:
        return _result(
            None, [], [],
            "not running in Kubernetes (no POD_NAMESPACE or service-account namespace)",
        )

    try:
        api = core_api_factory()
    except Exception as e:
        return _result(
            namespace, [], [],
            _describe_api_error("loading in-cluster Kubernetes config", e),
        )

    try:
        own_pod = api.read_namespaced_pod(name=own_pod_name, namespace=namespace)
    except Exception as e:
        return _result(
            namespace, [], [],
            _describe_api_error(f"reading own pod {namespace}/{own_pod_name}", e),
        )

    selector = label_selector_for(dict(own_pod.metadata.labels or {}))
    if selector:
        try:
            pod_list = api.list_namespaced_pod(
                namespace=namespace, label_selector=selector
            )
            pods = list(pod_list.items or [])
        except Exception as e:
            return _result(
                namespace, [], [],
                _describe_api_error(
                    f"listing pods in {namespace} with selector '{selector}'", e
                ),
            )
    else:
        pods = [own_pod]

    pods.sort(key=lambda p: p.metadata.name)
    error: Optional[str] = None
    if len(pods) > MAX_PODS:
        error = (
            f"selector '{selector}' matched {len(pods)} pods in {namespace}; "
            f"reporting the first {MAX_PODS}"
        )
        pods = pods[:MAX_PODS]
    summaries = [_summarize_pod(p) for p in pods]

    if request.pod_name:
        log_pods = [p for p in pods if p.metadata.name == request.pod_name]
        if not log_pods:
            return _result(
                namespace,
                summaries,
                [],
                f"pod '{request.pod_name}' is not a Holmes pod in {namespace} "
                f"(selector '{selector or 'own pod only'}')",
            )
    else:
        log_pods = pods

    if not request.include_logs:
        return _result(namespace, summaries, [], error)

    logs: List[Dict[str, Any]] = []
    for pod in log_pods:
        for container in pod.spec.containers or []:
            entry: Dict[str, Any] = {
                "pod": pod.metadata.name,
                "container": container.name,
                "previous": request.previous,
                "text": "",
                "truncated": False,
                "error": None,
            }
            try:
                text = api.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=namespace,
                    container=container.name,
                    tail_lines=request.tail_lines,
                    previous=request.previous,
                    _request_timeout=LOG_REQUEST_TIMEOUT_SECONDS,
                )
                entry["text"] = text or ""
            except Exception as e:
                entry["error"] = _describe_api_error(
                    f"reading {'previous ' if request.previous else ''}logs of "
                    f"{namespace}/{pod.metadata.name}/{container.name} "
                    f"(tail_lines={request.tail_lines})",
                    e,
                )
            logs.append(entry)

    return _result(namespace, summaries, logs, error)
