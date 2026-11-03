
import json
from typing import Any

class InvalidWebhookHeadersError(ValueError):
    """Raised when webhook headers are not a JSON object."""

def is_valid_webhook_header_value(headers: dict[str, Any]) -> bool:
    """Validate webhook header names and values."""

    for name, value in headers.items():
        if not isinstance(name, str) or not name:
            return False

        if not isinstance(value, str):
            return False
        
    return True


def resolve_webhook_headers(
    configured_headers: Any,
    env_headers: str | None,
) -> dict[str, Any]:
    """Resolve webhook headers from an explicit config value or an env value.

    An explicit (truthy) value wins. A missing/empty configured value falls back
    to ``env_headers``, which must be a JSON object. ``None`` inputs resolve to an
    empty mapping.
    """
    headers = configured_headers

    # Preserve the existing fallback behavior: an empty configured value falls back
    # to the environment-provided value.
    if not headers and env_headers:
        try:
            parsed = json.loads(env_headers)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidWebhookHeadersError(
                "WEBHOOK_HEADERS must be a JSON object"
            ) from exc
        if not isinstance(parsed, dict):
            raise InvalidWebhookHeadersError(
                "WEBHOOK_HEADERS must be a JSON object"
            )
        headers = parsed

    if headers is None:
        return {}

    if not isinstance(headers, dict) or not is_valid_webhook_header_value(headers=headers):
        raise InvalidWebhookHeadersError("Web hook headers must be a JSON object")

    return dict(headers)
