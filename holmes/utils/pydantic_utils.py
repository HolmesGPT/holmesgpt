import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Tuple, Type, Union, get_args, get_origin

import typer
from benedict import benedict  # type: ignore
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from holmes.plugins.prompts import load_prompt

PromptField = Annotated[str, BeforeValidator(lambda v: load_prompt(v))]

try:
    # pydantic v2
    from pydantic_core import PydanticUndefined  # type: ignore
except Exception:  # pragma: no cover
    PydanticUndefined = object()  # type: ignore


class RobustaBaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


def loc_to_dot_sep(loc: Tuple[Union[str, int], ...]) -> str:
    path = ""
    for i, x in enumerate(loc):
        if isinstance(x, str):
            if i > 0:
                path += "."
            path += x
        elif isinstance(x, int):
            path += f"[{x}]"
        else:
            raise TypeError("Unexpected type")
    return path


def convert_errors(e: ValidationError) -> List[Dict[str, Any]]:
    new_errors: List[Dict[str, Any]] = e.errors()  # type: ignore
    for error in new_errors:
        error["loc"] = loc_to_dot_sep(error["loc"])
    return new_errors


def load_model_from_file(
    model: Type[BaseModel], file_path: Path, yaml_path: Optional[str] = None
):
    try:
        contents = benedict(file_path, format="yaml")
        if yaml_path is not None:
            contents = contents[yaml_path]
        return model.model_validate(contents)
    except ValidationError as e:
        print(e)
        bad_fields = [e["loc"] for e in convert_errors(e)]
        typer.secho(
            f"Invalid config file at {file_path}. Check the fields {bad_fields}.\nSee detailed errors above.",
            fg="red",
        )
        sys.exit()

def build_config_example(model: Type[BaseModel] | BaseModel) -> Dict[str, Any]:
    """
    Build a JSON-serializable example object for a Pydantic model.

    Logic (in order):
    1) Keys are derived from the field/variable name.
    3) If available, use the first value from the field's examples array.
    2) Otherwise, use default or default_factory to generate the example value.
    4) If it's still None and the field type extends BaseModel, recursively build nested object example.
    5) Otherwise, use "your_<variable_name>" (even for lists, dicts, primitive, and any other type).
    """

    model_cls: Type[BaseModel] = model if isinstance(model, type) else model.__class__

    out: Dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_info.exclude:
            continue

        example_value: Any = None

        examples = getattr(field_info, "examples", None)
        if examples is None:
            json_schema_extra = getattr(field_info, "json_schema_extra", None) or {}
            examples = json_schema_extra.get("examples")

        if isinstance(examples, list) and len(examples) > 0:
            example_value = examples[0]

        if example_value is None:
            default = getattr(field_info, "default", PydanticUndefined)
            default_factory = getattr(field_info, "default_factory", None)

            if default is not PydanticUndefined:
                example_value = default
            elif default_factory is not None:
                try:
                    example_value = default_factory()
                except Exception:
                    # If a default factory can't be executed without context, fall back to other strategies.
                    example_value = None

        if example_value is None or isinstance(example_value, BaseModel):
            annotation = getattr(field_info, "annotation", None)
            nested_model_cls = _extract_base_model_subclass(annotation)
            if nested_model_cls is not None:
                example_value = build_config_example(nested_model_cls)

        if example_value is None:
            example_value = f"your_{field_name}"

        out[field_name] = example_value

    return out

def _extract_base_model_subclass(annotation: Any) -> Optional[Type[BaseModel]]:
    """
    Best-effort extraction of a BaseModel subclass from an annotation.

    Supports:
    - T where issubclass(T, BaseModel)
    - Optional[T] / Union[T, None]
    - Annotated[T, ...]
    """
    if annotation is None:
        return None

    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _extract_base_model_subclass(args[0])

    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]  # noqa: E721
        if len(args) == 1:
            return _extract_base_model_subclass(args[0])
        return None

    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except Exception:
        return None

    return None
