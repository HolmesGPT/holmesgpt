"""Classify typed transport failures without guessing from remote error text."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx
import requests


ToolErrorKind = Literal["timeout", "connection", "rate_limit", "unavailable", "permission", "invalid_arguments", "unknown"]
TRANSIENT_TOOL_ERRORS = frozenset({"timeout", "connection", "rate_limit", "unavailable"})


@dataclass(frozen=True)
class ToolErrorDetails:
    kind: ToolErrorKind = "unknown"
    retry_after_seconds: float | None = None


def classify_tool_error(exc: BaseException) -> ToolErrorDetails:
    # TaskGroup transports wrap failures. Mixed groups are not safely retryable.
    children = getattr(exc, "exceptions", ())
    if isinstance(children, (list, tuple)) and children:
        details = [classify_tool_error(child) for child in children if isinstance(child, BaseException)]
        if len(details) == len(children) and len({detail.kind for detail in details}) == 1:
            delays = [detail.retry_after_seconds for detail in details if detail.retry_after_seconds is not None]
            return ToolErrorDetails(details[0].kind, max(delays) if delays else None)
        return ToolErrorDetails()
    if isinstance(exc, (requests.HTTPError, httpx.HTTPStatusError)):
        response = exc.response
        if response is None:
            return ToolErrorDetails()
        code = response.status_code
        kind: ToolErrorKind = "unknown"
        if code == 408:
            kind = "timeout"
        elif code == 429:
            kind = "rate_limit"
        elif code in {401, 403}:
            kind = "permission"
        elif code in {400, 404, 405, 413, 422}:
            kind = "invalid_arguments"
        elif 500 <= code <= 599 and code not in {501, 505}:
            kind = "unavailable"
        delay = None
        header = response.headers.get("Retry-After", "")
        try:
            parsed = float(header)
            if math.isfinite(parsed) and parsed >= 0:
                delay = min(parsed, 3600)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(header)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = min(max(0, (retry_at - datetime.now(timezone.utc)).total_seconds()), 3600)
            except (TypeError, ValueError, OverflowError):
                pass
        return ToolErrorDetails(kind, delay)
    if isinstance(exc, (TimeoutError, requests.Timeout, httpx.TimeoutException)):
        return ToolErrorDetails("timeout")
    if isinstance(exc, requests.exceptions.SSLError):
        return ToolErrorDetails()
    if isinstance(exc, (ConnectionError, requests.ConnectionError, httpx.NetworkError)):
        return ToolErrorDetails("connection")
    if isinstance(exc, PermissionError):
        return ToolErrorDetails("permission")
    return ToolErrorDetails()
