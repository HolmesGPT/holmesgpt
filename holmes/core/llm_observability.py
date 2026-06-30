"""Map a request's identity into LiteLLM observability metadata.

HolmesGPT delegates LLM calls to LiteLLM, which forwards a reserved ``metadata``
field to whichever logging callback / LiteLLM proxy is configured (Langfuse,
Langsmith, Arize, Datadog LLM Observability, ...). Populating that field lets
every backend attribute a trace to the end user and group a conversation into a
session — without coupling Holmes to any single observability vendor.

This module is the single, deliberately narrow place that decides *what* of an
inbound request becomes observability metadata. It is a whitelist on purpose:
only known-safe identity fields are mapped to LiteLLM's documented metadata keys,
and free-form tags are bounded, so nothing arbitrary from ``request_context``
leaks into traces.
"""

from typing import Any, Dict, List, Optional

# Defensive bounds so a misbehaving caller cannot blow up trace cardinality.
_MAX_TAGS = 20
_MAX_TAG_LEN = 256


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_llm_metadata(
    request_context: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Build LiteLLM observability ``metadata`` from a request context.

    Maps Holmes' per-request identity to LiteLLM's documented, callback-agnostic
    metadata keys:

    - ``trace_user_id`` ← ``user_email`` (preferred) or ``user_id``
    - ``session_id``    ← ``conversation_id``
    - ``tags``          ← ``request_type:<...>`` and ``cluster:<...>`` when present

    Returns ``None`` when there is nothing to attribute, so callers can pass the
    result straight through to ``llm.completion(metadata=...)`` and the behaviour
    is unchanged for requests that carry no identity (e.g. the CLI).
    """
    if not request_context:
        return None

    metadata: Dict[str, Any] = {}

    user = _clean(request_context.get("user_email")) or _clean(
        request_context.get("user_id")
    )
    if user:
        metadata["trace_user_id"] = user

    session = _clean(request_context.get("conversation_id"))
    if session:
        metadata["session_id"] = session

    tags: List[str] = []
    request_type = _clean(request_context.get("request_type"))
    if request_type:
        tags.append(f"request_type:{request_type}")
    cluster_name = _clean(request_context.get("cluster_name"))
    if cluster_name:
        tags.append(f"cluster:{cluster_name}")
    if tags:
        metadata["tags"] = [tag[:_MAX_TAG_LEN] for tag in tags[:_MAX_TAGS]]

    return metadata or None
