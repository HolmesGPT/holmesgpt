"""Holmes's own pods and logs, for the platform's agent health / logs view.

Served as the ``holmes_logs`` internal request kind of the remote tool-call
worker (never as an LLM tool). Reads Holmes's own pods through the in-cluster
Kubernetes API; when that is denied or unavailable (``k8sRBAC: true``, no
service-account token, outside Kubernetes) it falls back to the in-process
ring buffer installed by :func:`install_memory_log_handler`.

The result is bounded: ``tail_lines`` is clamped to 1..5000 and the combined
log text to ``MAX_TOTAL_LOG_CHARS``, keeping the newest lines of each stream.
"""

import logging
import os
import socket
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, ConfigDict, field_validator

DEFAULT_TAIL_LINES = 500
MAX_TAIL_LINES = 5000
MEMORY_BUFFER_LINES = 2000
MAX_TOTAL_LOG_CHARS = 400_000
# Holmes runs a handful of replicas; this only guards against a selector that
# unexpectedly matches much more than Holmes.
MAX_PODS = 20
LOG_REQUEST_TIMEOUT_SECONDS = 20

SOURCE_KUBERNETES = "kubernetes"
SOURCE_MEMORY = "memory"
# Container name reported for in-process logs, which have no pod container.
MEMORY_CONTAINER_NAME = "holmes"

_SERVICEACCOUNT_NAMESPACE_FILE = Path(
    "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
)


class HolmesLogsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tail_lines: int = DEFAULT_TAIL_LINES
    previous: bool = False
    pod_name: Optional[str] = None
    include_logs: bool = True

    @field_validator("tail_lines", mode="before")
    @classmethod
    def _clamp_tail_lines(cls, value: Any) -> int:
        if value is None:
            return DEFAULT_TAIL_LINES
        return max(1, min(MAX_TAIL_LINES, int(value)))


# ---- in-process ring buffer ----


class RingBufferLogHandler(logging.Handler):
    """Keeps the last ``capacity`` formatted log lines of this process."""

    def __init__(self, capacity: int = MEMORY_BUFFER_LINES):
        super().__init__()
        self._lines: Deque[str] = deque(maxlen=capacity)
        self._lines_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self._lines_lock:
            # A multi-line record (tracebacks) counts as one entry so the
            # buffer bounds records, not physical lines.
            self._lines.append(text)

    def tail(self, n: int) -> List[str]:
        with self._lines_lock:
            lines = list(self._lines)
        return lines[-n:] if n > 0 else []


_memory_handler: Optional[RingBufferLogHandler] = None
_memory_handler_lock = threading.Lock()


def install_memory_log_handler(
    capacity: int = MEMORY_BUFFER_LINES,
) -> RingBufferLogHandler:
    """Attach the ring buffer to the root logger (idempotent).

    Call after logging is configured: ``basicConfig(force=True)`` removes
    root handlers installed before it.
    """
    global _memory_handler
    with _memory_handler_lock:
        root = logging.getLogger()
        if _memory_handler is None:
            _memory_handler = RingBufferLogHandler(capacity)
            _memory_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
        if _memory_handler not in root.handlers:
            root.addHandler(_memory_handler)
        return _memory_handler


def get_memory_log_handler() -> Optional[RingBufferLogHandler]:
    return _memory_handler


# ---- environment ----


def detect_pod_namespace() -> Optional[str]:
    env_val = os.environ.get("POD_NAMESPACE", "").strip()
    if env_val:
        return env_val
    try:
        if _SERVICEACCOUNT_NAMESPACE_FILE.is_file():
            content = _SERVICEACCOUNT_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
            if content:
                return content
    except OSError:
        logging.debug("Failed to read service-account namespace file", exc_info=True)
    return None


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


def _is_forbidden(e: Exception) -> bool:
    return isinstance(e, ApiException) and e.status in (401, 403)


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


def _result(
    source: str,
    namespace: Optional[str],
    pods: List[Dict[str, Any]],
    logs: List[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    _bound_logs(logs, MAX_TOTAL_LOG_CHARS)
    return {
        "source": source,
        "namespace": namespace,
        "pods": pods,
        "logs": logs,
        "error": error,
    }


def _memory_logs(request: HolmesLogsRequest, pod_name: str) -> List[Dict[str, Any]]:
    if not request.include_logs:
        return []
    if request.pod_name and request.pod_name != pod_name:
        return []
    handler = get_memory_log_handler()
    lines = handler.tail(request.tail_lines) if handler else []
    return [
        {
            "pod": pod_name,
            "container": MEMORY_CONTAINER_NAME,
            "previous": False,
            "text": "\n".join(lines),
            "truncated": False,
            "error": None,
        }
    ]


def _memory_fallback(
    request: HolmesLogsRequest,
    namespace: Optional[str],
    pod_name: str,
    reason: str,
    pods: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    notes = [reason, "showing this process's in-memory logs instead"]
    if request.previous:
        notes.append("previous container logs are unavailable from memory")
    if request.pod_name and request.pod_name != pod_name:
        notes.append(
            f"in-memory logs only cover pod '{pod_name}', not '{request.pod_name}'"
        )
    return _result(
        SOURCE_MEMORY,
        namespace,
        pods or [],
        _memory_logs(request, pod_name),
        "; ".join(notes),
    )


# ---- entry point ----


def get_holmes_logs(
    request: HolmesLogsRequest,
    core_api_factory: Callable[[], Any] = _default_core_api,
) -> Dict[str, Any]:
    namespace = detect_pod_namespace()
    own_pod_name = detect_pod_name()

    if not namespace:
        return _memory_fallback(
            request,
            None,
            own_pod_name,
            "not running in Kubernetes (no POD_NAMESPACE or service-account namespace)",
        )

    try:
        api = core_api_factory()
    except Exception as e:
        return _memory_fallback(
            request,
            namespace,
            own_pod_name,
            _describe_api_error("loading in-cluster Kubernetes config", e),
        )

    try:
        own_pod = api.read_namespaced_pod(name=own_pod_name, namespace=namespace)
    except Exception as e:
        return _memory_fallback(
            request,
            namespace,
            own_pod_name,
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
            return _memory_fallback(
                request,
                namespace,
                own_pod_name,
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
                SOURCE_KUBERNETES,
                namespace,
                summaries,
                [],
                f"pod '{request.pod_name}' is not a Holmes pod in {namespace} "
                f"(selector '{selector or 'own pod only'}')",
            )
    else:
        log_pods = pods

    if not request.include_logs:
        return _result(SOURCE_KUBERNETES, namespace, summaries, [], error)

    logs: List[Dict[str, Any]] = []
    forbidden = 0
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
                if _is_forbidden(e):
                    forbidden += 1
                entry["error"] = _describe_api_error(
                    f"reading {'previous ' if request.previous else ''}logs of "
                    f"{namespace}/{pod.metadata.name}/{container.name} "
                    f"(tail_lines={request.tail_lines})",
                    e,
                )
            logs.append(entry)

    if logs and forbidden == len(logs):
        return _memory_fallback(
            request,
            namespace,
            own_pod_name,
            f"reading pod logs in {namespace} is forbidden (pods/log)",
            pods=summaries,
        )

    return _result(SOURCE_KUBERNETES, namespace, summaries, logs, error)
