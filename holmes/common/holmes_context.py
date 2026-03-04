import contextvars
from typing import Any, Dict, Optional

_holmes_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "holmes_context", default={}
)


def _get_holmes_context_value(key: str) -> Any:
    return _holmes_context.get().get(key)


def _set_holmes_context_value(
    key: str, value: Any
) -> contextvars.Token[Dict[str, Any]]:
    ctx = dict(_holmes_context.get())
    ctx[key] = value
    return _holmes_context.set(ctx)


def get_feature_id() -> Optional[str]:
    return _get_holmes_context_value("feature_id") or "holmes_unknown"


def set_feature_id(value: str) -> contextvars.Token[Dict[str, Any]]:
    return _set_holmes_context_value("feature_id", value)
