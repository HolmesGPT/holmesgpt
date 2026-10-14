"""Sanitized description of a failed LLM run, safe to store and show.

Providers put their own words in the exception ("model capacity exhausted"),
next to things that must not be persisted: API keys they echo back, emails,
internal hosts and IPs, account ids. The provider's sentence is kept verbatim
after redaction; everything else is reduced to metadata (class, status code,
provider).
"""

import ast
import html
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

MAX_REASON_CHARS = 500
MAX_CLASS_CHARS = 80
# Bounds regex work on a pathological message; the reason is cut to
# MAX_REASON_CHARS after redaction.
_MAX_INPUT_CHARS = 20_000

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HTML = re.compile(r"<\s*(?:!doctype|html|head|body|h[1-6]|p|div)\b", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_OPAQUE_TOKEN = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")

# litellm wraps the provider's text as "litellm.<Err>: <Err>: <Provider>Exception - <text>",
# and the OpenAI client adds "Error code: <status> - <body>".
_ERROR_PREFIX = re.compile(
    r"^(?:(?:litellm\.[A-Za-z_]+|[A-Za-z_.]*(?:Error|Exception)):\s+)+"
)
_PROVIDER_PREFIX = re.compile(r"^[A-Za-z]*Exception\s+-\s+")
_STATUS_PREFIX = re.compile(r"^Error code:\s*\d+\s*-\s*")
_MESSAGE_KEYS = ("reason", "message", "msg", "detail")

_REDACTIONS = (
    (
        re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s\"',;]+"),
        "Authorization: [REDACTED]",
    ),
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), "[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_KEY]"),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
        ),
        "[REDACTED_KEY]",
    ),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[REDACTED_KEY]"),
    (re.compile(r"(://)[^\s/@:]+:[^\s/@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(
            r"""(?ix)\b((?:x-)?api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret
            |secret|password|passwd|pwd|token|signature|sig)(["']?\s*[:=]\s*["']?)
            (?!\[REDACTED)[^\s"',;&}]+"""
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"(?i)([?&]key=)[^&\s]+"), r"\1[REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b"), "[REDACTED_IP]"),
    (
        re.compile(
            r"\b(?:[A-Za-z0-9-]+\.)+(?:cluster\.local|internal|local|corp|lan|intranet|svc|home|private)\b(?::\d+)?"
        ),
        "[REDACTED_HOST]",
    ),
    (re.compile(r"\borg-[A-Za-z0-9]{6,}"), "org-[REDACTED]"),
    (re.compile(r"(arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:)\d{12}"), r"\1[REDACTED]"),
)

_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")
_THROTTLE_MARKER = "Model is getting throttled"
_MAX_CAUSE_DEPTH = 5


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def _unwrap_body(text: str) -> str:
    """The message inside a JSON / dict-repr error body, or the text itself."""
    if not text.startswith("{"):
        return text
    body: Any = None
    for parse in (json.loads, ast.literal_eval):
        try:
            body = parse(text)
            break
        except Exception:
            continue
    if not isinstance(body, dict):
        return text
    nested = body.get("error")
    for source in (nested if isinstance(nested, dict) else None, body):
        if source is None:
            continue
        for key in _MESSAGE_KEYS:
            if isinstance(source.get(key), str) and source[key].strip():
                return source[key]
    return text


def _redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return _OPAQUE_TOKEN.sub(_redact_opaque_token, text)


def _redact_opaque_token(match: "re.Match[str]") -> str:
    token = match.group(0)
    if _UUID.fullmatch(token):
        return token
    if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
        return "[REDACTED_TOKEN]"
    return token


def sanitize_error_text(value: Any, limit: int = MAX_REASON_CHARS) -> str:
    """The provider's own sentence, redacted, on one line, at most ``limit`` chars."""
    text = _as_text(value)[:_MAX_INPUT_CHARS].strip()
    text = _ERROR_PREFIX.sub("", text, count=1)
    text = _PROVIDER_PREFIX.sub("", text, count=1)
    text = _STATUS_PREFIX.sub("", text, count=1)
    text = _unwrap_body(text.strip())
    if _HTML.search(text):
        text = html.unescape(_TAG.sub(" ", text))
    text = _CONTROL.sub(" ", _ANSI.sub("", text))
    text = " ".join(text.split())
    text = _redact(text)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CAUSE_DEPTH:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _valid_status(value: Any) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _valid_provider(value: Any) -> Optional[str]:
    if isinstance(value, str) and _PROVIDER.fullmatch(value):
        return value
    return None


def _class_name(exc: BaseException) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:MAX_CLASS_CHARS]
    return name or "Exception"


@dataclass(frozen=True)
class ErrorInfo:
    error_class: str
    status_code: Optional[int]
    provider: Optional[str]
    reason: str

    @property
    def from_provider(self) -> bool:
        """The failure came from the LLM provider or the Robusta platform in
        front of it, so its sentence is theirs and safe to keep. Anything else
        (a toolset bug, a DB error) is reduced to its class."""
        return self.status_code is not None or self.provider is not None

    @classmethod
    def from_exception(cls, exc: BaseException) -> "ErrorInfo":
        status_code: Optional[int] = None
        provider: Optional[str] = None
        for link in _chain(exc):
            status_code = status_code or _valid_status(getattr(link, "status_code", None))
            provider = provider or _valid_provider(getattr(link, "llm_provider", None))
        try:
            message = str(exc)
        except Exception:
            message = ""
        error_class = _class_name(exc)
        if _THROTTLE_MARKER in message and status_code is None:
            # Bedrock raises a bare Exception for throttling.
            error_class = "RateLimitError"
            provider = provider or "bedrock"
        return cls(
            error_class=error_class,
            status_code=status_code,
            provider=provider,
            reason=sanitize_error_text(message),
        )

    def event_fields(self) -> Dict[str, Any]:
        """Fields for the error event the client receives."""
        fields: Dict[str, Any] = {"error_class": self.error_class}
        if self.status_code is not None:
            fields["provider_status"] = self.status_code
        if self.provider is not None:
            fields["provider"] = self.provider
        return fields

    def meta_fields(self) -> Dict[str, Any]:
        """Keys for ``HolmesUsageEvents.meta``."""
        fields: Dict[str, Any] = {"error_class": self.error_class}
        if self.status_code is not None:
            fields["error_status_code"] = self.status_code
        if self.provider is not None:
            fields["error_provider"] = self.provider
        return fields
