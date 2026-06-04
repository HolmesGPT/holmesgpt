"""Logging utilities for Holmes."""

import logging
import re
from typing import Any, Optional

REDACTED = "[REDACTED]"

# Credential-bearing key names that show up in URLs, query strings, headers and
# JSON bodies (e.g. LiteLLM appends `?key=<gemini-key>` to the request URL, and
# httpx echoes that URL back inside HTTPStatusError messages / tracebacks).
_SECRET_KEY_NAMES = (
    r"api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|"
    r"client[_-]?secret|secret|password|passwd|pwd|authorization|auth|key"
)

# `?key=...` / `&api_key=...` in URLs and query strings. Stops at the next query
# separator or whitespace/quote so only the credential value is masked.
_QUERY_PARAM_RE = re.compile(
    rf"(?i)([?&](?:{_SECRET_KEY_NAMES})=)[^&\s\"']+"
)

# `key=value`, `key: value`, `"key": "value"` in headers / JSON / kwargs dumps.
_KEY_VALUE_RE = re.compile(
    rf"(?i)([\"']?\b(?:{_SECRET_KEY_NAMES})\b[\"']?\s*[:=]\s*[\"']?)"
    r"[^\s,&\"'}{]+"
)

# `Bearer <token>` / `Basic <token>` authorization header values.
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]+")

# Well-known provider key formats, redacted by value regardless of context so a
# leaked key is masked even when it appears without a recognizable key name.
_KNOWN_KEY_FORMATS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google / Gemini
    re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"),  # Anthropic
    re.compile(r"sk-[0-9A-Za-z_\-]{20,}"),  # OpenAI (incl. sk-proj-)
]


def redact_secrets(text: Optional[str]) -> Optional[str]:
    """Mask credentials (API keys, tokens, passwords) in an arbitrary string.

    Used to scrub log messages and exception tracebacks before they reach log
    backends. Best-effort: it targets common credential patterns rather than
    guaranteeing every secret is caught.
    """
    if not text or not isinstance(text, str):
        return text

    text = _QUERY_PARAM_RE.sub(rf"\1{REDACTED}", text)
    text = _BEARER_RE.sub(rf"\1 {REDACTED}", text)
    for pattern in _KNOWN_KEY_FORMATS:
        text = pattern.sub(REDACTED, text)
    text = _KEY_VALUE_RE.sub(rf"\1{REDACTED}", text)
    return text


class SecretRedactingFilter(logging.Filter):
    """Logging filter that strips credentials from messages and tracebacks.

    Attached to the root logger's handlers so it covers every log record,
    including exceptions logged with ``exc_info=True`` (the formatted traceback
    is redacted and cached on the record so downstream handlers reuse it).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Render the message with its args FIRST, then redact the final string.
        # This catches secrets nested inside dict/list/object args (which
        # str-only scrubbing of record.args would miss, e.g.
        # logging.error("%s", {"authorization": "Bearer ..."})) and avoids
        # corrupting the format string (redacting a "%s" that happens to sit
        # next to a credential pattern before interpolation would break
        # "msg % args"). After rendering we clear args so downstream handlers
        # don't re-interpolate.
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact_secrets(message)
        record.args = ()

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact_secrets(record.exc_text)

        return True


def install_secret_redaction(logger: Optional[logging.Logger] = None) -> None:
    """Attach a :class:`SecretRedactingFilter` to a logger's handlers.

    Defaults to the root logger. Idempotent: a handler that already has the
    filter is left untouched.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(
            isinstance(existing, SecretRedactingFilter)
            for existing in handler.filters
        ):
            handler.addFilter(SecretRedactingFilter())


class EndpointFilter(logging.Filter):
    """Filter out log records for specific endpoint paths."""

    def __init__(self, path: str, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1
