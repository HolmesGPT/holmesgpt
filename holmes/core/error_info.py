"""What a failed LLM run records: where the error came from, not how to debug it.

Only fields that are positively identified are kept: the exception class, the
provider's status code and error code, and a short provider message. Nothing is
scrubbed; whatever does not qualify is dropped. In particular a message is kept
only when it is plain prose (see ``short_message``), so it cannot carry keys,
ids, hosts, paths or trace text.
"""

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

from holmes.core.relay_refusal import RelayRefusal

MAX_MESSAGE_CHARS = 200
MAX_SENTENCES = 2
MAX_WORDS_PER_SENTENCE = 30
MAX_CLASS_CHARS = 80
# Larger bodies are not parsed; their message is simply not extracted.
_MAX_PARSE_CHARS = 64_000
_MAX_CAUSE_DEPTH = 5

# litellm wraps the provider's text as "litellm.<Err>: <Err>: <Provider>Exception - <text>",
# and the OpenAI client adds "Error code: <status> - <body>".
_ERROR_PREFIX = re.compile(
    r"^(?:(?:litellm\.[A-Za-z_]+|[A-Za-z_.]*(?:Error|Exception)):\s+)+"
)
_PROVIDER_PREFIX = re.compile(r"^[A-Za-z]*Exception\s+-\s+")
_STATUS_PREFIX = re.compile(r"^Error code:\s*\d+\s*-\s*")
# litellm appends this to str(e) when it retried; it is not part of the body.
_RETRY_SUFFIX = re.compile(
    r"\s*LiteLLM Retried: \d+ times(?:, LiteLLM Max Retries: \d+)?\s*$"
)

# The keys the backend owns in HolmesUsageEvents.meta; clients cannot set them.
ERROR_META_KEYS = (
    "error_class",
    "error_status_code",
    "error_provider",
    "error_provider_code",
)

# `code` first, then `type`; AWS puts it in `__type` as "namespace#Name".
_CODE_KEYS = ("provider_code", "code", "type", "__type")
# `msg` is relay's own error body and `detail` a FastAPI one; both still go
# through the prose gate. `provider_message` is the provider's and comes first.
_MESSAGE_KEYS = ("provider_message", "message", "msg", "detail")

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,47}")
# Anthropic wraps every error in {"type": "error", ...}; that says nothing.
_GENERIC_CODES = frozenset({"error"})
# A word is plain letters: no digits, hyphens, slashes, '@' or ':' can appear,
# so identifiers, hosts, paths, emails and keys never qualify.
_WORD = re.compile(r"(?:[A-Z]?[a-z']{1,14}|[A-Z]{2,6})")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")
_LLM_MODULES = ("litellm", "openai", "anthropic")
_THROTTLE_MARKER = "Model is getting throttled"


def _sentence_is_prose(sentence: str) -> bool:
    body = sentence[:-1] if sentence[-1:] in ".!?" else sentence
    words = body.split(" ")
    if not 1 <= len(words) <= MAX_WORDS_PER_SENTENCE:
        return False
    return all(_WORD.fullmatch(w[:-1] if w.endswith(",") else w) for w in words)


def short_message(text: Any) -> Optional[str]:
    """The leading sentences of ``text`` if, and only as far as, they are plain
    prose; ``None`` when not even the first one is. Only the first line is
    considered, so anything after a newline (a traceback) is never read."""
    if not isinstance(text, str):
        return None
    first_line = text.strip().split("\n", 1)[0].strip()
    kept = []
    for sentence in _SENTENCE_SPLIT.split(first_line):
        if len(kept) == MAX_SENTENCES or not _sentence_is_prose(sentence):
            break
        kept.append(sentence)
    message = " ".join(kept)
    if not message or len(message) > MAX_MESSAGE_CHARS:
        return None
    return message


def _code(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.rsplit("#", 1)[-1]
    if value.lower() in _GENERIC_CODES:
        return None
    if _CODE.fullmatch(value) and sum(c.isdigit() for c in value) <= 8:
        return value
    return None


def _parse_body(text: str) -> Optional[dict]:
    if not text.startswith("{") or len(text) > _MAX_PARSE_CHARS:
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            body = parse(text)
        except Exception:
            continue
        return body if isinstance(body, dict) else None
    return None


def _from_body(body: dict) -> Tuple[Optional[str], Optional[str]]:
    """``(code, message)`` read field by field from a provider error body."""
    nested = body.get("error")
    code = message = None
    for source in (nested if isinstance(nested, dict) else None, body):
        if source is None:
            continue
        code = code or next((c for c in map(_code, map(source.get, _CODE_KEYS)) if c), None)
        message = message or next(
            (m for m in map(short_message, map(source.get, _MESSAGE_KEYS)) if m), None
        )
    return code, message


def _clean_text(raw: str) -> str:
    # Bound the input first: _RETRY_SUFFIX is quadratic on long whitespace runs.
    text = _RETRY_SUFFIX.sub("", raw[: _MAX_PARSE_CHARS + 1].strip(), count=1)
    text = _ERROR_PREFIX.sub("", text, count=1)
    text = _PROVIDER_PREFIX.sub("", text, count=1)
    return _STATUS_PREFIX.sub("", text, count=1).strip()


def _extract_text(raw: str) -> Tuple[Optional[str], Optional[str], Optional[dict]]:
    """``(code, message, body)`` from an exception's text; ``body`` is the
    parsed JSON / dict body when there was one."""
    text = _clean_text(raw)
    body = _parse_body(text)
    if body is None:
        return None, short_message(text), None
    code, message = _from_body(body)
    return code, message, body


def parse_provider_text(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """``(code, message)`` the provider reported in an exception's text.

    A JSON / dict body is read field by field; anything else is only a message
    candidate as a whole."""
    code, message, _ = _extract_text(raw)
    return code, message


def _structured_body(exc: BaseException) -> Optional[dict]:
    """The error body litellm kept on the exception, or on the response it
    wrapped. Preferred over the exception's text, which litellm decorates."""
    body = _attr(exc, "body")
    if isinstance(body, dict):
        return body
    response = _attr(exc, "response")
    try:
        body = response.json() if response is not None else None
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _is_llm_error(exc: BaseException) -> bool:
    """Raised by an LLM client library, or by the platform in front of it.
    Only these carry a ``status_code`` / ``llm_provider`` worth trusting."""
    return isinstance(exc, RelayRefusal) or any(
        cls.__module__.split(".")[0] in _LLM_MODULES for cls in type(exc).__mro__
    )


def _attr(obj: Any, name: str) -> Any:
    """``getattr`` that survives a property which raises: this runs inside
    error handlers, which must not fail with a different error."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _text(exc: BaseException) -> str:
    try:
        return str(exc)
    except Exception:
        return ""


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
    code: Optional[str] = None
    message: Optional[str] = None

    @property
    def from_provider(self) -> bool:
        """The failure came from the LLM provider or the Robusta platform in
        front of it. Anything else (a toolset bug, a DB error) is reduced to
        its class."""
        return self.status_code is not None or self.provider is not None

    @classmethod
    def from_exception(cls, exc: BaseException) -> "ErrorInfo":
        status_code: Optional[int] = None
        provider: Optional[str] = None
        code: Optional[str] = None
        message: Optional[str] = None
        first_llm_link = True
        for link in _chain(exc):
            if not _is_llm_error(link):
                continue
            status_code = status_code or _valid_status(_attr(link, "status_code"))
            provider = provider or _valid_provider(_attr(link, "llm_provider"))
            if first_llm_link:
                body = _structured_body(link)
                code, message = _from_body(body) if body else (None, None)
                if code is None and message is None:
                    code, message, text_body = _extract_text(_text(link))
                    body = text_body or body
                # Relay names the provider it called; litellm only sees relay.
                # Its error_class marks the body as relay's, so an arbitrary
                # upstream body cannot relabel the provider.
                if body and body.get("error_class") and _valid_provider(body.get("provider")):
                    provider = body["provider"]
                first_llm_link = False
        error_class = _class_name(exc)
        if (
            type(exc) is Exception
            and _THROTTLE_MARKER in _text(exc)
            and status_code is None
        ):
            # Bedrock raises a bare Exception for throttling.
            error_class = "RateLimitError"
            provider = provider or "bedrock"
        return cls(error_class, status_code, provider, code, message)

    def summary(self) -> str:
        """One line for the surfaces that render the event's text: what failed
        and where, then the provider's message when there is one."""
        details = [str(d) for d in (self.status_code, self.provider, self.code) if d]
        head = f"AI provider error: {self.error_class}"
        if details:
            head += f" ({', '.join(details)})"
        return f"{head}. {self.message}" if self.message else f"{head}."

    def event_fields(self) -> Dict[str, Any]:
        """Fields for the error event the client receives."""
        fields: Dict[str, Any] = {"error_class": self.error_class}
        if self.status_code is not None:
            fields["provider_status"] = self.status_code
        if self.provider is not None:
            fields["provider"] = self.provider
        if self.code is not None:
            fields["provider_code"] = self.code
        return fields

    def meta_fields(self) -> Dict[str, Any]:
        """Keys for ``HolmesUsageEvents.meta``."""
        fields: Dict[str, Any] = {"error_class": self.error_class}
        if self.status_code is not None:
            fields["error_status_code"] = self.status_code
        if self.provider is not None:
            fields["error_provider"] = self.provider
        if self.code is not None:
            fields["error_provider_code"] = self.code
        return fields
