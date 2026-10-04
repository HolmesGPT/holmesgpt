"""Schema validation without network reference resolution."""

from typing import Any

from jsonschema import Draft202012Validator


def validator(schema: dict[str, Any]) -> Draft202012Validator:
    def local_references(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and isinstance(child, str) and not child.startswith("#"):
                    raise ValueError("External schema references are unsupported")
                local_references(child)
        elif isinstance(value, list):
            for child in value:
                local_references(child)
    local_references(schema)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)
