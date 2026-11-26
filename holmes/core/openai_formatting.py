import copy
import re
from typing import Any

from holmes.common.env_vars import (
    STRICT_TOOL_CALLS_ENABLED,
    TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS,
)

# parses both simple types: "int", "array", "string"
# but also arrays of those simpler types: "array[int]", "array[string]", etc.
pattern = r"^(array\[(?P<inner_type>\w+)\])|(?P<simple_type>\w+)$"


def _ensure_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively enforce OpenAI strict-mode constraints on a JSON Schema dict.

    - Sets ``additionalProperties: false`` on every object that has ``properties``.
    - Ensures ``required`` lists all property names (strict mode requires it).
    - Recurses into nested objects, array items, and anyOf/oneOf branches.

    Returns a shallow-copied schema so the caller's original is not mutated.
    """
    schema = dict(schema)  # shallow copy top level

    schema_type = schema.get("type")

    if schema_type == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())
        schema["properties"] = {
            k: _ensure_strict_schema(v) for k, v in schema["properties"].items()
        }

    if "items" in schema and isinstance(schema["items"], dict):
        schema["items"] = _ensure_strict_schema(schema["items"])

    for keyword in ("anyOf", "oneOf"):
        if keyword in schema and isinstance(schema[keyword], list):
            schema[keyword] = [
                _ensure_strict_schema(branch) if isinstance(branch, dict) else branch
                for branch in schema[keyword]
            ]

    return schema


def apply_strict_mode(result: dict[str, Any]) -> dict[str, Any]:
    """Apply strict-mode envelope to a fully-built OpenAI tool definition.

    Sets ``strict: true`` on the function and enforces
    ``additionalProperties: false`` recursively on the parameters schema.
    Shared by both the ToolParameter pipeline and the frontend raw-schema path.
    """
    result = copy.deepcopy(result)
    func = result.get("function", {})
    func["strict"] = True
    if "parameters" in func:
        func["parameters"] = _ensure_strict_schema(func["parameters"])
    return result


def _is_tool_strict_compatible(tool_parameters: dict) -> bool:
    """Check if all parameters in a tool are compatible with strict mode."""
    for param in tool_parameters.values():
        if hasattr(param, "is_strict_compatible") and not param.is_strict_compatible():
            return False
    return True


def type_to_open_ai_schema(param_attributes: Any, strict_mode: bool) -> dict[str, Any]:
    schema = _build_parameter_schema(param_attributes, strict_mode)
    description = getattr(param_attributes, "description", None)
    if description is not None:
        schema["description"] = description

    return schema


def _build_parameter_schema(param_attributes: Any, strict_mode: bool) -> dict[str, Any]:
    enum = getattr(param_attributes, "enum", None)
    strict_optional = strict_mode and not param_attributes.required

    # Handle union types (anyOf with multiple non-null branches) first.
    if hasattr(param_attributes, "any_of") and param_attributes.any_of:
        branches = [
            type_to_open_ai_schema(branch, strict_mode)
            for branch in param_attributes.any_of
        ]
        if not param_attributes.required:
            branches.append({"type": "null"})
        schema: dict[str, Any] = {"anyOf": branches}
        if enum:
            enum_values = list(enum)
            if strict_optional and None not in enum_values:
                enum_values.append(None)
            schema["enum"] = enum_values
        return schema

    # Normalize schema types: MCP servers may emit nullable lists (e.g., ["string", "null"])
    # per JSON Schema spec, while OpenAI expects a primary type with explicit nullability via anyOf.
    raw_type = param_attributes.type
    is_nullable_from_schema = False

    if isinstance(raw_type, list):
        non_null_types = [t.strip() if isinstance(t, str) else t for t in raw_type if t != "null"]
        is_nullable_from_schema = "null" in raw_type
        param_type = non_null_types[0] if non_null_types else "string"
    else:
        param_type = raw_type.strip()

    type_obj: dict[str, Any]

    if param_type == "object":
        type_obj = {"type": "object"}

        # Use explicit properties if provided
        if hasattr(param_attributes, "properties") and param_attributes.properties:
            type_obj["properties"] = {
                name: type_to_open_ai_schema(prop, strict_mode)
                for name, prop in param_attributes.properties.items()
            }
            if strict_mode:
                type_obj["required"] = list(param_attributes.properties.keys())
                type_obj["additionalProperties"] = False

        # Preserve additionalProperties schema for dynamic-key objects
        elif hasattr(param_attributes, "additional_properties") and param_attributes.additional_properties not in (None, False):
            type_obj["additionalProperties"] = param_attributes.additional_properties
        elif strict_mode:
            type_obj["additionalProperties"] = False

    elif param_type == "array":
        # Handle arrays with explicit item schemas
        if hasattr(param_attributes, "items") and param_attributes.items:
            items_schema = type_to_open_ai_schema(param_attributes.items, strict_mode)
            type_obj = {"type": "array", "items": items_schema}
        else:
            # Fallback for arrays without explicit item schema
            type_obj = {"type": "array", "items": {"type": "object"}}
            if strict_mode:
                type_obj["items"]["additionalProperties"] = False
    else:
        match = re.match(pattern, param_type)

        if not match:
            raise ValueError(f"Invalid type format: {param_type}")

        if match.group("inner_type"):
            inner_type = match.group("inner_type")
            if inner_type == "object":
                raise ValueError(
                    "object inner type must have schema. Use ToolParameter.items"
                )
            else:
                type_obj = {"type": "array", "items": {"type": inner_type}}
        else:
            type_obj = {"type": match.group("simple_type")}

    # Merge passthrough JSON Schema keywords (minItems, maxItems, minimum, etc.)
    # so the LLM sees validation constraints from the source schema.
    extra = getattr(param_attributes, "json_schema_extra", None)
    if extra:
        type_obj.update(extra)

    if not (is_nullable_from_schema or strict_optional):
        if enum:
            type_obj["enum"] = list(enum)
        return type_obj

    null_schema: dict[str, Any] = {"type": "null"}
    if enum:
        # Vertex's nullable conversion reads the enum from the typed branch.
        non_null_enum = [value for value in enum if value is not None]
        if not non_null_enum:
            if "default" in type_obj:
                null_schema["default"] = type_obj["default"]
            return null_schema
        type_obj["enum"] = non_null_enum
        # A source enum can disallow null even when its type includes null.
        if not strict_optional and None not in enum:
            return type_obj

    return {"anyOf": [type_obj, null_schema]}


def format_tool_to_open_ai_standard(
    tool_name: str, tool_description: str, tool_parameters: dict
):
    # Strict mode is enabled globally unless disabled via HOLMES_DISABLE_STRICT_TOOL_CALLS.
    # However, tools with dynamic-key objects (additionalProperties with a schema) are
    # automatically excluded from strict mode since both OpenAI and Anthropic require
    # additionalProperties: false on all objects in strict mode.
    strict_mode = STRICT_TOOL_CALLS_ENABLED and _is_tool_strict_compatible(tool_parameters)

    tool_properties = {
        param_name: type_to_open_ai_schema(param_attributes, strict_mode)
        for param_name, param_attributes in tool_parameters.items()
    }

    result: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_description,
            "parameters": {
                "properties": tool_properties,
                "required": [
                    param_name
                    for param_name, param_attributes in tool_parameters.items()
                    if param_attributes.required or strict_mode
                ],
                "type": "object",
            },
        },
    }

    if strict_mode:
        result = apply_strict_mode(result)

    # gemini doesnt have parameters object if it is without params
    if TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS and (
        tool_properties is None or tool_properties == {}
    ):
        result["function"].pop("parameters")  # type: ignore

    return result
