import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Tuple, Type, Union

import typer
from benedict import benedict  # type: ignore
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from holmes.plugins.prompts import load_prompt

PromptField = Annotated[str, BeforeValidator(lambda v: load_prompt(v))]


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
    except ValueError as e:
        print(e)
        typer.secho(
            f"Invalid content in config file at {file_path}. Ensure the content of this file is in normal yaml format.")
        sys.exit()
