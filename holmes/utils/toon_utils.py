import logging
from typing import Any

from pydantic import BaseModel
from toon_format import encode

logger = logging.getLogger(__name__)


def _normalize_for_toon(value: Any) -> Any:
    """
    Normalize arbitrary python objects into data structures supported by the
    TOON encoder.
    """
    if isinstance(value, BaseModel):
        # Convert pydantic models to plain dicts
        value = value.model_dump(mode="json")

    if isinstance(value, dict):
        return {str(key): _normalize_for_toon(val) for key, val in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_normalize_for_toon(item) for item in value]

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    # Strings are already supported by the encoder
    if isinstance(value, str):
        return value

    # As a last resort, fall back to stringifying unsupported objects so that the
    # encoded output still captures their value instead of silently dropping them.
    return str(value)


def encode_to_toon(value: Any, *, indent: int = 2) -> str:
    """
    Encode a python object into the TOON format while handling non-serializable
    values gracefully.
    """
    normalized_value = _normalize_for_toon(value)
    try:
        return encode(normalized_value, {"indent": indent})
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.debug(
            "Failed to encode value to TOON, falling back to string: %s", exc, exc_info=True
        )
        return str(normalized_value)
