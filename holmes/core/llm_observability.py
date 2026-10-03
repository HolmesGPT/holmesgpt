"""Derive end-user / session attribution for an LLM call from a request.

Holmes runs as a server on behalf of many users. Attaching the end-user
identifier and the conversation/session to each LLM call lets any observability
backend group and filter traces by user and conversation — regardless of which
model provider (OpenAI, Anthropic, Bedrock, ...) actually serves the request.

Attribution is expressed with provider-neutral fields:

- ``user`` is the standard end-user identifier understood across providers.
  Unlike ``metadata``, it is forwarded all the way to the model provider (see
  ``DefaultLLM.completion()``), so it is a stable hash of the identifier rather
  than the raw value — the same guidance OpenAI gives for this field ("we
  recommend hashing their username or email to avoid sending us any
  identifying information"). The hash is deterministic, so a given user still
  maps to a single, filterable trace identity.
- ``metadata`` carries additional, optional observability fields (session id,
  tags) that Holmes forwards without interpreting them. Unlike ``user``, this
  is a logging-only field consumed by whichever process makes the call — it is
  not sent to the model provider — so it does not need hashing here.

This module is the single, deliberately narrow place that decides *what* of an
inbound request becomes attribution data. It is a whitelist on purpose: only
known-safe identity fields are mapped, and free-form tags are bounded, so nothing
arbitrary from ``request_context`` leaks into traces.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Defensive bounds so a misbehaving caller cannot blow up trace cardinality.
_MAX_TAGS = 20
_MAX_TAG_LEN = 256


@dataclass(frozen=True)
class TraceAttribution:
    """Provider-neutral attribution for a single LLM call."""

    user: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def is_empty(self) -> bool:
        return self.user is None and not self.metadata


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _hash_identifier(value: str) -> str:
    """Stable, one-way hash for a value forwarded to the model provider.

    Deterministic (same input always yields the same output) so traces from
    the same user still group together, without exposing the raw identifier
    to whichever provider serves the request.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_trace_attribution(
    request_context: Optional[Dict[str, Any]],
) -> TraceAttribution:
    """Build provider-neutral trace attribution from a request context.

    - ``user``     ← sha256(``user_email`` (preferred) or ``user_id``)
    - ``session_id`` (metadata) ← ``conversation_id``
    - ``tags`` (metadata)       ← ``request_type:<...>`` and ``cluster:<...>``

    Returns an empty :class:`TraceAttribution` when there is nothing to
    attribute, so behaviour is unchanged for callers that carry no identity
    (e.g. the CLI).
    """
    if not request_context:
        return TraceAttribution()

    raw_user = _clean(request_context.get("user_email")) or _clean(
        request_context.get("user_id")
    )
    user = _hash_identifier(raw_user) if raw_user else None

    metadata: Dict[str, Any] = {}
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

    return TraceAttribution(user=user, metadata=metadata or None)
