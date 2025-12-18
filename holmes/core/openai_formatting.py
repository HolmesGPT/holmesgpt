import re
from typing import Any, Optional

from holmes.common.env_vars import (
    TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS,
    LLMS_WITH_STRICT_TOOL_CALLS,
)
from holmes.utils.llms import model_matches_list

# parses both simple types: "int", "array", "string"
# but also arrays of those simpler types: "array[int]", "array[string]", etc.
pattern = r"^(array\[(?P<inner_type>\w+)\])|(?P<simple_type>\w+)$"

LLMS_WITH_STRICT_TOOL_CALLS_LIST = [
    llm.strip() for llm in LLMS_WITH_STRICT_TOOL_CALLS.split(",")
]


def type_to_open_ai_schema(param_attributes: Any, strict_mode: bool) -> dict[str, Any]:
    param_type = param_attributes.type.strip()
    type_obj: Optional[dict[str, Any]] = None

    if param_type == "object":
        type_obj = {"type": "object"}
        if strict_mode:
            type_obj["additionalProperties"] = False

        # Use explicit properties if provided
        if hasattr(param_attributes, "properties") and param_attributes.properties:
            type_obj["properties"] = {
                name: type_to_open_ai_schema(prop, strict_mode)
                for name, prop in param_attributes.properties.items()
            }
            if strict_mode:
                type_obj["required"] = list(param_attributes.properties.keys())

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

    if strict_mode and type_obj and not param_attributes.required:
        type_obj["type"] = [type_obj["type"], "null"]

    return type_obj


def format_tool_to_open_ai_standard(
    tool_name: str, tool_description: str, tool_parameters: dict, target_model: str
):
    tool_properties = {}

    strict_mode = model_matches_list(target_model, LLMS_WITH_STRICT_TOOL_CALLS_LIST)

    for param_name, param_attributes in tool_parameters.items():
        tool_properties[param_name] = type_to_open_ai_schema(
            param_attributes=param_attributes, strict_mode=strict_mode
        )
        if param_attributes.description is not None:
            tool_properties[param_name]["description"] = param_attributes.description
        # Add enum constraint if specified
        if hasattr(param_attributes, "enum") and param_attributes.enum:
            enum_values = list(
                param_attributes.enum
            )  # Create a copy to avoid modifying original
            # In strict mode, optional parameters need None in their enum to match the type allowing null
            if (
                strict_mode
                and not param_attributes.required
                and None not in enum_values
            ):
                enum_values.append(None)
            tool_properties[param_name]["enum"] = enum_values

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

    if strict_mode and result["function"]:
        result["function"]["strict"] = True
        result["function"]["parameters"]["additionalProperties"] = False

    # gemini doesnt have parameters object if it is without params
    if TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS and (
        tool_properties is None or tool_properties == {}
    ):
        result["function"].pop("parameters")  # type: ignore

    return result
