from pathlib import Path
from typing import Literal, get_args

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


# Read pytest marks from pyproject.toml dynamically
def _get_allowed_eval_tags():
    pyproject_path = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        pyproject = tomllib.load(f)

    markers = (
        pyproject.get("tool", {})
        .get("pytest", {})
        .get("ini_options", {})
        .get("markers", [])
    )

    # Extract tag names from marker definitions
    # Skip "llm" marker as it's a special marker for all LLM tests
    tags = []
    for marker in markers:
        if ":" in marker and not marker.startswith("llm:"):
            tag_name = marker.split(":")[0].strip()
            tags.append(tag_name)

    # Sort tags for consistency
    tags.sort()

    # Create a Literal type with all discovered tags
    # If no tags found, return a Literal with empty string to avoid type errors
    if not tags:
        return Literal[""]

    # Dynamically create Literal type with unpacking
    return Literal[tuple(tags)]


ALLOWED_EVAL_TAGS = _get_allowed_eval_tags()


# For debugging/inspection - get actual tag values
def get_allowed_tags_list():
    """Returns the list of allowed tags as strings"""
    return list(get_args(ALLOWED_EVAL_TAGS))
