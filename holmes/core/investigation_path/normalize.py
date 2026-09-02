"""Turn executed tool calls into canonical, redacted path events.

This is the only place that reads real tool-call data, so it is also the only
place that has to enforce the schema's redaction rules. Three of them are worth
stating outright, because they are easy to break by accident later:

- **Only allow-listed parameters are read at all.** Anything not on one of the
  key lists below is never looked at, so a new toolset cannot leak a parameter
  into stored paths just by naming it something new.
- **Tool output is never read.** `StructuredToolResult.data` is not touched.
  Error text *is* read, but only to pick an `ErrorClass`, and is then dropped.
- **Values that could be secrets are dropped, not stored.** Sensitive keys are
  skipped, and high-entropy words inside shell commands are replaced.
"""

import re
import shlex
from typing import Any, Dict, List, Optional, Sequence, Tuple

from holmes.core.investigation_path.schema import (
    EntityRef,
    ErrorClass,
    InvestigationPath,
    Outcome,
    OutcomeStatus,
    PathEvent,
    QueryIntent,
    TimeWindow,
    bucket_lookback_seconds,
)

# A shell command is reduced to at most this many words. Enough to keep
# "kubectl rollout history deployment catalog-service" - the longest shape that
# still names a single resource - and short enough to leave flags and pipelines
# out. Anything past the resource name is argument noise.
MAX_COMMAND_WORDS = 5

# ---------------------------------------------------------------------------
# Parameter allow-lists. Anything outside these is never read.
# ---------------------------------------------------------------------------
_KIND_PARAM_KEYS = ("kind", "resource_type", "resource_kind", "resource")
_NAME_PARAM_KEYS = (
    "name",
    "resource_name",
    "pod_name",
    "pod",
    "service_name",
    "deployment_name",
    "workload",
)
_NAMESPACE_PARAM_KEYS = ("namespace", "ns")
_QUERY_PARAM_KEYS = ("query", "promql", "expr", "logql")
_COMMAND_PARAM_KEYS = ("command", "cmd")
_LOOKBACK_PARAM_KEYS = ("since", "lookback", "duration", "period", "range", "time_range")
_START_PARAM_KEYS = ("start", "start_time", "from")
_END_PARAM_KEYS = ("end", "end_time", "to")

# Substrings that mark a parameter as possibly secret. Matching keys are
# skipped even if they also appear on an allow-list above.
_SENSITIVE_KEY_TOKENS = (
    "api_key",
    "apikey",
    "auth",
    "bearer",
    "cert",
    "cookie",
    "credential",
    "passwd",
    "password",
    "private",
    "secret",
    "session",
    "signature",
    "token",
)

_REDACTED = "<redacted>"

# ---------------------------------------------------------------------------
# Kubernetes naming
# ---------------------------------------------------------------------------
_RESOURCE_ALIASES = {
    "cj": "cronjob",
    "cronjobs": "cronjob",
    "cm": "configmap",
    "configmaps": "configmap",
    "ds": "daemonset",
    "daemonsets": "daemonset",
    "deploy": "deployment",
    "deployments": "deployment",
    "ep": "endpoints",
    "endpoint": "endpoints",
    "events": "event",
    "hpa": "horizontalpodautoscaler",
    "horizontalpodautoscalers": "horizontalpodautoscaler",
    "ing": "ingress",
    "ingresses": "ingress",
    "jobs": "job",
    "no": "node",
    "nodes": "node",
    "ns": "namespace",
    "namespaces": "namespace",
    "po": "pod",
    "pods": "pod",
    "pv": "persistentvolume",
    "persistentvolumes": "persistentvolume",
    "pvc": "persistentvolumeclaim",
    "persistentvolumeclaims": "persistentvolumeclaim",
    "netpol": "networkpolicy",
    "networkpolicies": "networkpolicy",
    "rs": "replicaset",
    "replicasets": "replicaset",
    "secrets": "secret",
    "sts": "statefulset",
    "statefulsets": "statefulset",
    "svc": "service",
    "services": "service",
}

# Kinds that describe how components connect rather than a single workload.
_TOPOLOGY_KINDS = frozenset({"service", "endpoints", "ingress", "networkpolicy"})

# Every kind the command parser will recognize. Deriving this from the alias
# table alone silently excludes any canonical kind that happens to have no
# abbreviation, which reads as "not a kind at all" and sends the name into the
# entity as if it were a resource name.
_KNOWN_KINDS = frozenset(_RESOURCE_ALIASES.values()) | _TOPOLOGY_KINDS

# Verbs that take a sub-verb before the resource, as in
# `kubectl rollout history deployment/catalog-service`. Without this the
# sub-verb is read as the kind and the whole target ends up in the name.
_COMMAND_SUB_VERBS = {
    "rollout": frozenset({"history", "status", "undo", "restart", "pause", "resume"}),
    "auth": frozenset({"can-i"}),
}

_PROMQL_KEYWORDS = frozenset(
    {
        "abs", "absent", "and", "avg", "avg_over_time", "bool", "bottomk", "by",
        "ceil", "changes", "clamp_max", "clamp_min", "count", "count_over_time",
        "count_values", "delta", "deriv", "floor", "group_left", "group_right",
        "histogram_quantile", "idelta", "ignoring", "increase", "irate",
        "label_join", "label_replace", "last_over_time", "max", "max_over_time",
        "min", "min_over_time", "offset", "on", "or", "predict_linear",
        "quantile", "rate", "resets", "round", "scalar", "sort", "sort_desc",
        "stddev", "stdvar", "sum", "sum_over_time", "time", "timestamp", "topk",
        "unless", "vector", "without",
    }
)

# Flags whose *next* token is a value rather than part of the check.
_VALUE_FLAGS = frozenset(
    {
        "-c", "-l", "-n", "-o", "--container", "--context", "--field-selector",
        "--namespace", "--output", "--selector", "--since", "--tail",
    }
)

_SHELL_SEPARATORS = frozenset({"|", "||", "&&", ";", ">", ">>"})

_IDENTIFIER_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])?\s*$", re.IGNORECASE)

_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# ---------------------------------------------------------------------------
# Intent inference
# ---------------------------------------------------------------------------
# Checked in order; the first tool-name substring that matches wins.
_INTENT_BY_TOOL_KEYWORD: Tuple[Tuple[Tuple[str, ...], QueryIntent], ...] = (
    (("log",), QueryIntent.LOGS),
    (("event",), QueryIntent.EVENTS),
    (("trace", "span", "tempo"), QueryIntent.TRACES),
    (("metric", "prometheus", "promql", "query_range", "victoriametrics"), QueryIntent.METRICS),
    (("top", "usage", "utilization", "recommendation", "capacity"), QueryIntent.RESOURCE_USAGE),
    (("endpoint", "connectivity", "network", "lineage", "topology"), QueryIntent.TOPOLOGY),
    (("change", "rollout", "revision", "helm", "argocd", "history"), QueryIntent.CONFIG_HISTORY),
    (("describe", "get_by_name", "detail"), QueryIntent.DESCRIBE),
    (("list", "count", "find", "search", "jq_query", "tabular_query"), QueryIntent.LIST),
)

# Shell verbs take priority over tool name for command-running tools, because
# every such call has the same tool name.
_INTENT_BY_COMMAND_VERB = {
    "describe": QueryIntent.DESCRIBE,
    "events": QueryIntent.EVENTS,
    "logs": QueryIntent.LOGS,
    "rollout": QueryIntent.CONFIG_HISTORY,
    "top": QueryIntent.RESOURCE_USAGE,
}

_ERROR_KEYWORDS: Tuple[Tuple[Tuple[str, ...], ErrorClass], ...] = (
    (("not found", "notfound", "404", "no such"), ErrorClass.NOT_FOUND),
    (("forbidden", "permission denied", "unauthorized", "401", "403"), ErrorClass.FORBIDDEN),
    (("timed out", "timeout", "deadline exceeded"), ErrorClass.TIMEOUT),
    (("invalid", "parse error", "bad request", "syntax", "400"), ErrorClass.INVALID_QUERY),
    (("unavailable", "connection refused", "503", "502"), ErrorClass.UNAVAILABLE),
)

_UNUSABLE_STATUSES = frozenset({"approval_required", "frontend_pause"})


# ---------------------------------------------------------------------------
# Name and kind normalization
# ---------------------------------------------------------------------------
def looks_like_instance_id(token: str) -> bool:
    """True for generated suffixes: a ReplicaSet hash or a StatefulSet ordinal."""
    if not token:
        return False
    if token.isdigit():
        return True
    if len(token) < 5:
        return False
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def normalize_resource_name(name: str) -> str:
    """Strip the generated part of a name so instances of one workload match.

    At most two suffixes are removed, which is what Kubernetes appends: a
    ReplicaSet hash plus a pod suffix.
    """
    cleaned = name.strip().lower()
    if not cleaned:
        return ""
    parts = cleaned.split("-")
    for _ in range(2):
        if len(parts) > 1 and looks_like_instance_id(parts[-1]):
            parts.pop()
        else:
            break
    return "-".join(parts)


def normalize_resource_kind(kind: str) -> str:
    cleaned = kind.strip().lower()
    return _RESOURCE_ALIASES.get(cleaned, cleaned)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def _looks_high_entropy(word: str) -> bool:
    """Rough test for an inline secret: long, mixed-case or mixed-class, no dashes."""
    if len(word) < 20 or "-" in word or "/" in word:
        return False
    has_digit = any(c.isdigit() for c in word)
    has_alpha = any(c.isalpha() for c in word)
    return has_digit and has_alpha


def _read_param(params: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    """Read the first allow-listed key present, skipping anything secret-looking."""
    for key in keys:
        if _is_sensitive_key(key):
            continue
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------
def _command_words(command: str) -> List[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes: split naively and drop stray quote characters so
        # the same command still maps to the same check.
        tokens = [token.strip("'\"") for token in command.split()]

    words: List[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _SHELL_SEPARATORS:
            break
        if token.startswith("-"):
            skip_next = token in _VALUE_FLAGS
            continue
        words.append(_REDACTED if _looks_high_entropy(token) else token)
        if len(words) == MAX_COMMAND_WORDS:
            break
    return words


def split_resource_target(token: str) -> Tuple[Optional[str], Optional[str]]:
    """Split kubectl's `kind/name` shorthand into its two parts.

    `deployment/catalog-service` and `deployment catalog-service` are the same
    check, so they have to produce the same entity. Returns `(None, None)` for
    anything that is not that shape, including a bare name and a path-like
    argument such as `-f ./manifests/app.yaml`.
    """
    kind, separator, name = token.partition("/")
    if not separator or not kind or not name or "/" in name:
        return None, None
    normalized_kind = normalize_resource_kind(kind)
    if normalized_kind not in _KNOWN_KINDS:
        return None, None
    return normalized_kind, normalize_resource_name(name)


def _entity_from_command_words(words: Sequence[str]) -> EntityRef:
    """Read `<binary> <verb> [<sub-verb>] [<kind>] [<name>]` out of a command."""
    if len(words) < 3:
        return EntityRef()

    verb = words[1]
    rest = list(words[2:])

    # `rollout history deploy/x`: the sub-verb sits where the kind would be, so
    # the resource is one word further along than usual.
    sub_verbs = _COMMAND_SUB_VERBS.get(verb)
    if sub_verbs and rest[0] in sub_verbs:
        rest = rest[1:]
    if not rest:
        return EntityRef()

    kind, name = split_resource_target(rest[0])
    if kind:
        return EntityRef(kind=kind, name=name)

    kind = normalize_resource_kind(rest[0])
    if kind in _KNOWN_KINDS:
        name = normalize_resource_name(rest[1]) if len(rest) > 1 else None
        return EntityRef(kind=kind, name=name or None)

    # A bare name. `kubectl logs my-pod` names a pod without saying so.
    if verb in ("logs", "log"):
        return EntityRef(kind="pod", name=normalize_resource_name(rest[0]))
    return EntityRef(name=normalize_resource_name(rest[0]))


def _metric_from_query(query: str) -> Optional[str]:
    """Pull the queried metric out of a PromQL/LogQL expression."""
    for match in _IDENTIFIER_RE.finditer(query):
        token = match.group(0)
        if token.lower() in _PROMQL_KEYWORDS:
            continue
        return token
    return None


def _parse_duration_seconds(value: str) -> Optional[float]:
    match = _DURATION_RE.match(value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    return amount * _DURATION_UNIT_SECONDS[unit]


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------
def _infer_intent(tool_name: str, command_words: Sequence[str], entity: EntityRef) -> QueryIntent:
    if command_words:
        verb = command_words[1] if len(command_words) > 1 else ""
        if verb in _INTENT_BY_COMMAND_VERB:
            return _INTENT_BY_COMMAND_VERB[verb]
        if verb == "get":
            if entity.kind in _TOPOLOGY_KINDS:
                return QueryIntent.TOPOLOGY
            return QueryIntent.DESCRIBE if entity.name else QueryIntent.LIST

    lowered = tool_name.lower()
    for keywords, intent in _INTENT_BY_TOOL_KEYWORD:
        if any(keyword in lowered for keyword in keywords):
            return intent

    if entity.kind in _TOPOLOGY_KINDS:
        return QueryIntent.TOPOLOGY
    if entity.name:
        return QueryIntent.DESCRIBE
    if entity.kind:
        return QueryIntent.LIST
    return QueryIntent.OTHER


def _entity_from_params(params: Dict[str, Any]) -> EntityRef:
    kind = _read_param(params, _KIND_PARAM_KEYS)
    name = _read_param(params, _NAME_PARAM_KEYS)
    namespace = _read_param(params, _NAMESPACE_PARAM_KEYS)

    if kind or name:
        return EntityRef(
            kind=normalize_resource_kind(kind) if kind else None,
            name=normalize_resource_name(name) if name else None,
            namespace=normalize_resource_name(namespace) if namespace else None,
        )

    query = _read_param(params, _QUERY_PARAM_KEYS)
    if query:
        metric = _metric_from_query(query)
        return EntityRef(
            kind="metric" if metric else None,
            name=metric,
            namespace=normalize_resource_name(namespace) if namespace else None,
        )
    return EntityRef(namespace=normalize_resource_name(namespace) if namespace else None)


def _time_window_from_params(params: Dict[str, Any], intent: QueryIntent) -> Optional[TimeWindow]:
    lookback = _read_param(params, _LOOKBACK_PARAM_KEYS)
    if lookback:
        seconds = _parse_duration_seconds(lookback)
        bucketed = bucket_lookback_seconds(seconds)
        if bucketed is not None:
            return TimeWindow(lookback_seconds=bucketed)

    # Explicit start/end are absolute timestamps and therefore unstable. Only
    # their span is kept.
    start = _read_param(params, _START_PARAM_KEYS)
    end = _read_param(params, _END_PARAM_KEYS)
    if start and end:
        span = _span_seconds(start, end)
        bucketed = bucket_lookback_seconds(span)
        if bucketed is not None:
            return TimeWindow(lookback_seconds=bucketed)

    if intent in (QueryIntent.DESCRIBE, QueryIntent.LIST, QueryIntent.TOPOLOGY):
        return TimeWindow(is_point_in_time=True)
    return None


def _span_seconds(start: str, end: str) -> Optional[float]:
    try:
        return abs(float(end) - float(start))
    except (TypeError, ValueError):
        return None


def classify_error(error_text: Optional[str]) -> ErrorClass:
    """Collapse a raw error into a class. The text itself is never stored."""
    if not error_text:
        return ErrorClass.OTHER
    lowered = error_text.lower()
    for keywords, error_class in _ERROR_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return error_class
    return ErrorClass.OTHER


def _read_tool_call(tool_call: Any) -> Tuple[Optional[str], Dict[str, Any], Optional[str], Optional[str], Optional[str]]:
    """Read (tool_name, params, status, error, evidence_ref) from a tool call.

    Accepts both `ToolCallResult` objects and the client dicts produced by
    `ToolCallResult.to_client_dict()`.
    """
    if isinstance(tool_call, dict):
        tool_name = tool_call.get("tool_name") or tool_call.get("name")
        evidence_ref = tool_call.get("tool_call_id")
        result: Any = tool_call.get("result")
    else:
        tool_name = getattr(tool_call, "tool_name", None)
        evidence_ref = getattr(tool_call, "tool_call_id", None)
        result = getattr(tool_call, "result", None)

    if isinstance(result, dict):
        params = result.get("params")
        status = result.get("status")
        error = result.get("error")
    else:
        params = getattr(result, "params", None)
        status = getattr(result, "status", None)
        error = getattr(result, "error", None)

    if status is not None and not isinstance(status, str):
        status = getattr(status, "value", None)
    if not isinstance(params, dict):
        params = {}
    if error is not None and not isinstance(error, str):
        error = str(error)
    return tool_name, params, status, error, evidence_ref


def event_from_tool_call(tool_call: Any, ordinal: int) -> Optional[PathEvent]:
    """Build one canonical path event, or None if the call is not a real check."""
    tool_name, params, status, error, evidence_ref = _read_tool_call(tool_call)
    if not tool_name:
        return None
    if status and status.lower() in _UNUSABLE_STATUSES:
        return None

    command = _read_param(params, _COMMAND_PARAM_KEYS)
    command_words = _command_words(command) if command else []

    entity = _entity_from_params(params)
    if not entity.kind and not entity.name and command_words:
        command_entity = _entity_from_command_words(command_words)
        entity = EntityRef(
            kind=command_entity.kind,
            name=command_entity.name,
            namespace=entity.namespace,
        )

    intent = _infer_intent(tool_name, command_words, entity)

    if status == "error":
        outcome = Outcome(status=OutcomeStatus.ERROR, error_class=classify_error(error))
    elif status == "no_data":
        outcome = Outcome(status=OutcomeStatus.NO_DATA)
    else:
        outcome = Outcome(status=OutcomeStatus.SUCCESS)

    return PathEvent(
        ordinal=ordinal,
        tool=tool_name,
        intent=intent,
        entity=entity,
        time_window=_time_window_from_params(params, intent),
        outcome=outcome,
        evidence_ref=evidence_ref,
    )


def path_from_tool_calls(tool_calls: Optional[Sequence[Any]]) -> InvestigationPath:
    """Reduce an investigation's tool calls to a canonical, ordered, redacted path."""
    events: List[PathEvent] = []
    for tool_call in tool_calls or []:
        event = event_from_tool_call(tool_call, ordinal=len(events))
        if event is not None:
            events.append(event)
    return InvestigationPath(events=events)
