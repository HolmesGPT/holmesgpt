"""Coerce LLM tool-call parameters to match their JSON Schema types.

## Why this exists

LLMs frequently return tool-call arguments with wrong types — especially when
strict mode is disabled.  Common mistakes include:

- Stringified JSON for array/object params: ``'["a","b"]'`` instead of ``["a","b"]``
- String numbers: ``"42"`` instead of ``42``
- String booleans: ``"true"`` instead of ``true``
- A bare value where an array is expected: ``"cpu"`` instead of ``["cpu"]``

These mismatches cause hard-to-diagnose failures in downstream tool code.
This module provides a single ``coerce_params`` function that fixes them.

## Why not Pydantic TypeAdapter?

We evaluated using ``pydantic.TypeAdapter`` to coerce values against
dynamically-built Python types.  It handles numeric/bool coercion well, but:

1. **JSON-stringified arrays/objects** — the most common LLM mistake — are NOT
   handled by ``validate_python()``.  Pydantic sees a ``str`` and rejects it
   against ``list[str]``.  ``validate_json()`` requires the *entire* input to
   be a JSON string, which doesn't match our param-dict structure.
2. **Single-value → array wrapping** (``"cpu"`` → ``["cpu"]``) is not
   supported by Pydantic at all.
3. **Lossy coercion risk**: in lax mode Pydantic silently truncates floats to
   ints (``3.7`` → ``3``), which could corrupt data.
4. Translating ``ToolParameter`` → Python type annotations dynamically adds
   complexity and a maintenance surface for no clear gain.

Given that we need custom pre-processing for (1) and (2) regardless, layering
Pydantic on top only for numeric/bool coercion adds dependency coupling without
eliminating hand-written logic.  A small, explicit, well-tested utility is
simpler and safer.

## Design principles

- **Schema-driven**: every coercion is gated on the ``primary_type`` declared
  in the JSON Schema.  We never guess.
- **Non-destructive**: returns a shallow copy; the caller's dict is untouched.
- **Conservative**: when in doubt, leave the value alone and let the tool
  report its own error.  We never silently truncate or lose precision.
- **Strict flag**: callers can opt in to *only* structural coercions
  (stringified JSON → parsed) while skipping lossy scalar conversions.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# JSON Schema type string → Python type(s) that are considered "already correct"
_EXPECTED_PYTHON_TYPES: Dict[str, tuple] = {
    "array": (list,),
    "object": (dict,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "string": (str,),
}


def _primary_type(schema: Any) -> str:
    """Extract the primary (non-null) type from a schema-like object.

    Accepts either a ``ToolParameter`` (which has a ``primary_type`` property)
    or a plain dict with a ``"type"`` key (for future use with raw JSON Schema
    dicts).
    """
    if hasattr(schema, "primary_type"):
        return schema.primary_type

    raw = schema.get("type", "string") if isinstance(schema, dict) else "string"
    if isinstance(raw, list):
        non_null = [t for t in raw if t != "null"]
        return non_null[0] if non_null else "string"
    return raw


def _coerce_single_value(
    name: str,
    value: Any,
    expected: str,
    *,
    strict: bool,
    tool_name: str,
) -> Any:
    """Attempt to coerce *value* to *expected* JSON Schema type.

    Returns the coerced value, or the original value unchanged if coercion is
    not applicable or would be lossy.

    When ``strict=True``, only structural coercions are applied (stringified
    JSON → parsed JSON, single value → array wrap).  Scalar type coercions
    (string → int, string → bool, etc.) are skipped because they can be lossy
    or ambiguous.
    """
    # --- Already the right type? Nothing to do. ---
    expected_types = _EXPECTED_PYTHON_TYPES.get(expected, ())
    if expected_types and isinstance(value, expected_types):
        # Guard: Python bool is a subclass of int.  If we expect an int and
        # got a bool, that's still a mismatch worth leaving alone (the tool
        # may care about the distinction).
        if expected == "integer" and isinstance(value, bool):
            pass  # fall through to coercion attempts
        else:
            return value

    # --- Stringified JSON → array / object ---
    # This is the single most common LLM mistake and is always safe to fix.
    if isinstance(value, str) and expected in ("array", "object"):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if expected == "array" and isinstance(parsed, list):
            logger.debug("Coerced param '%s' from string to array for tool '%s'", name, tool_name)
            return parsed
        if expected == "object" and isinstance(parsed, dict):
            logger.debug("Coerced param '%s' from string to object for tool '%s'", name, tool_name)
            return parsed

    # --- Single value → array wrap ---
    # LLM sends "cpu" instead of ["cpu"].  Safe: wrapping never loses data.
    if expected == "array" and not isinstance(value, list):
        logger.debug("Coerced param '%s' by wrapping single value in array for tool '%s'", name, tool_name)
        return [value]

    # --- Below this point: scalar coercions that strict mode skips. ---
    if strict:
        return value

    # --- String → integer ---
    if expected == "integer" and isinstance(value, str):
        try:
            as_float = float(value)
            # Only coerce if the value is actually a whole number.
            # "3.7" should NOT silently become 3.
            if as_float == int(as_float):
                coerced = int(as_float)
                logger.debug("Coerced param '%s' from string to integer for tool '%s'", name, tool_name)
                return coerced
        except (ValueError, OverflowError):
            pass
        return value

    # --- String → number (float) ---
    if expected == "number" and isinstance(value, str):
        try:
            coerced = float(value)
            logger.debug("Coerced param '%s' from string to number for tool '%s'", name, tool_name)
            return coerced
        except (ValueError, OverflowError):
            pass
        return value

    # --- String → boolean ---
    # Only accept unambiguous canonical representations.
    # We intentionally do NOT coerce "0"/"1" — too ambiguous.
    if expected == "boolean" and isinstance(value, str):
        lower = value.lower()
        if lower == "true":
            logger.debug("Coerced param '%s' from string to boolean for tool '%s'", name, tool_name)
            return True
        if lower == "false":
            logger.debug("Coerced param '%s' from string to boolean for tool '%s'", name, tool_name)
            return False
        return value

    return value


def coerce_params(
    params: Dict[str, Any],
    schema: Dict[str, Any],
    *,
    strict: bool = False,
    tool_name: str = "",
) -> Dict[str, Any]:
    """Coerce *params* to match the types declared in *schema*.

    Parameters
    ----------
    params:
        The raw parameter dict from the LLM tool call.
    schema:
        A mapping of ``{param_name: ToolParameter | dict}`` describing the
        expected JSON Schema type of each parameter.  Each value must either
        expose a ``primary_type`` property (``ToolParameter``) or be a dict
        with a ``"type"`` key.
    strict:
        When ``True``, only apply structural coercions (stringified JSON
        parsing and single-value array wrapping).  Scalar coercions (string →
        int/float/bool) are skipped.  Use this when you want maximum safety
        and only need to fix the most common LLM serialization mistakes.
    tool_name:
        Used in log messages to identify which tool's params are being coerced.

    Returns
    -------
    A shallow copy of *params* with coerced values.  The original dict is
    never mutated.
    """
    if not schema or not params:
        return params

    coerced: Optional[Dict[str, Any]] = None  # lazy-copy on first change

    for name, param_schema in schema.items():
        if name not in params:
            continue

        value = params[name]
        expected = _primary_type(param_schema)

        new_value = _coerce_single_value(
            name,
            value,
            expected,
            strict=strict,
            tool_name=tool_name,
        )

        if new_value is not value:
            if coerced is None:
                coerced = dict(params)
            coerced[name] = new_value

    return coerced if coerced is not None else dict(params)
