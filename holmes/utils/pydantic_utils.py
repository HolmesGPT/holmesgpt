import logging
import sys
from pathlib import Path
from typing import (
    Annotated,
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

import typer
from benedict import benedict  # type: ignore
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from holmes.plugins.prompts import load_prompt

logger = logging.getLogger(__name__)

try:
    # pydantic v2
    from pydantic_core import PydanticUndefined  # type: ignore
except Exception:  # pragma: no cover
    PydanticUndefined = object()  # type: ignore

PromptField = Annotated[str, BeforeValidator(lambda v: load_prompt(v))]


class ToolsetConfig(BaseModel):
    """
    Base class for toolset configuration models with backward compatibility support.

    Subclasses can define a `_deprecated_mappings` class variable to map old field names
    to new field names. When old field names are used in configuration, they will be
    automatically transformed to the new names and a deprecation warning will be logged.

    Example:
        class MyToolsetConfig(ToolsetConfig):
            _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
                "old_field_name": "new_field_name",  # Rename old_field_name to new_field_name
                "removed_field": None,  # Field was removed, accept but ignore
            }
            new_field_name: str = Field(...)
    """

    model_config = ConfigDict(extra="allow")

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {}
    _name: ClassVar[Optional[str]] = None
    _description: ClassVar[Optional[str]] = None
    _icon_url: ClassVar[Optional[str]] = None
    _docs_anchor: ClassVar[Optional[str]] = None
    _hidden_fields: ClassVar[List[str]] = []
    # Fields that should appear as required in the exported JSON schema
    # even though they're declared Optional at the Pydantic level. Use this
    # when a field MUST be filled in via the frontend form, but the backend
    # needs to accept it missing (e.g. because an env var, managed identity,
    # or runtime fallback populates it later). The UI marks the field as
    # required; Pydantic stays permissive so CLI / Helm configs with
    # alternative credential sources keep working.
    _ui_required_fields: ClassVar[List[str]] = []
    _recommended: ClassVar[bool] = False
    # Stable identifier for this variant, used to populate meta.subtype on the
    # synced HolmesToolsStatus row so the frontend can distinguish catalog
    # cards that share a backend toolset name (e.g. Prometheus vs VictoriaMetrics,
    # both backed by prometheus/metrics).
    _subtype: ClassVar[Optional[str]] = None

    @classmethod
    def has_required_fields(cls) -> bool:
        """Check if this config class has any required fields (fields without defaults)."""
        for field_name, field_info in cls.model_fields.items():
            default = getattr(field_info, "default", PydanticUndefined)
            default_factory = getattr(field_info, "default_factory", None)
            if default is PydanticUndefined and default_factory is None:
                return True
        return False

    @classmethod
    def build_schema_entry(cls) -> Dict[str, Any]:
        """Build the UI-facing schema entry for this config class.

        Honors `_hidden_fields` (stripped from the schema's `properties` and
        `required`) and `_ui_required_fields` (added to `required` even when
        the field is Optional at the Pydantic level), and packs in the
        display metadata: `_name`, `_description`, `_icon_url`,
        `_docs_anchor`, `_recommended`, `_subtype`.

        `Toolset.get_config_schema` calls this for each entry in
        `config_classes` to build the per-toolset map sent to the frontend.
        Lives here (next to the ClassVars it consumes) so that subclasses
        can extend the contract — adding a new ClassVar means updating one
        method on this class, not hunting through `Toolset` for the reader.
        """
        raw_schema = cls.model_json_schema()

        hidden = list(cls._hidden_fields or [])
        if hidden:
            props = raw_schema.get("properties", {})
            for name in hidden:
                props.pop(name, None)
            if "required" in raw_schema:
                raw_schema["required"] = [
                    r for r in raw_schema["required"] if r not in hidden
                ]

        # Don't re-mark fields that were just hidden as required.
        ui_required = [
            f for f in (cls._ui_required_fields or []) if f not in hidden
        ]
        if ui_required:
            existing = list(raw_schema.get("required", []))
            # Preserve declaration order: keep existing entries first, then
            # append any ui_required fields that aren't already required.
            seen = set(existing)
            for name in ui_required:
                if name not in seen:
                    existing.append(name)
                    seen.add(name)
            raw_schema["required"] = existing

        return {
            "schema": raw_schema,
            "name": cls._name or cls.__name__,
            "description": cls._description,
            "icon_url": cls._icon_url,
            "docs_anchor": cls._docs_anchor,
            "recommended": bool(cls._recommended),
            # Stable slug the frontend emits as the top-level `subtype:` YAML
            # field so the backend can pick this exact variant. None for
            # toolsets that don't use variants.
            "subtype": cls._subtype,
        }

    @model_validator(mode="before")
    @classmethod
    def handle_deprecated_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        mappings = cls._deprecated_mappings
        if not mappings:
            return data

        deprecated_used = []
        for old_name, new_name in mappings.items():
            if old_name in data:
                if new_name is not None:
                    # Only migrate if the new field is not already set
                    if new_name not in data:
                        data[new_name] = data.pop(old_name)
                        deprecated_used.append(f"{old_name} -> {new_name}")
                    else:
                        # New field takes precedence, just remove old field
                        data.pop(old_name)
                        deprecated_used.append(f"{old_name} -> {new_name}")
                else:
                    # Field was removed, just log and remove
                    data.pop(old_name)
                    deprecated_used.append(f"{old_name} (removed)")

        if deprecated_used:
            logger.warning(
                f"{cls.__name__} uses deprecated field names. "
                f"Please update: {', '.join(deprecated_used)}"
            )

        return data

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

    # Honor _hidden_fields (set on ToolsetConfig subclasses) so hidden fields
    # are excluded from the generated YAML example in the same way they are
    # excluded from the schema returned to the frontend.
    hidden_fields = set(getattr(model_cls, "_hidden_fields", []) or [])

    out: Dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_info.exclude or field_name in hidden_fields:
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
