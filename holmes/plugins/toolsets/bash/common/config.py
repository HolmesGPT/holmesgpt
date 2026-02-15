import logging
from typing import Any, List, Literal

from pydantic import Field, model_validator

from holmes.utils.pydantic_utils import ToolsetConfig

# Hardcoded blocks - these patterns are ALWAYS blocked and cannot be overridden
HARDCODED_BLOCKS: List[str] = [
    "sudo",
    "su",
]


class BashExecutorConfig(ToolsetConfig):
    """Configuration for the bash toolset with prefix-based validation."""

    # Allow/deny lists for prefix-based command validation
    allow: List[str] = Field(
        default_factory=list,
        title="Allow List",
        description="Additional command prefixes to allow (merged with builtin allowlist)",
    )
    deny: List[str] = Field(
        default_factory=list,
        title="Deny List",
        description="Command prefixes to deny (takes precedence over allow list)",
    )

    # Controls which builtin allowlist to use:
    # - "core" (CLI default): kubectl read-only, jq, grep, text processing, system info
    # - "extended" (Helm default): core + filesystem commands (cat, find, ls, base64)
    # - "none": empty builtin list, user manages their own via `allow`
    builtin_allowlist: Literal["none", "core", "extended"] = Field(
        default="core",
        title="Builtin Allowlist",
        description='Which builtin allowlist to include: "none", "core", or "extended"',
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_deprecated_include_default(cls, data: Any) -> Any:
        """Migrate deprecated include_default_allow_deny_list to builtin_allowlist."""
        if not isinstance(data, dict):
            return data

        if "include_default_allow_deny_list" in data:
            old_val = data.pop("include_default_allow_deny_list")
            if "builtin_allowlist" not in data:
                data["builtin_allowlist"] = "extended" if old_val else "none"
            logging.warning(
                "Deprecated bash config field 'include_default_allow_deny_list'. "
                "Please update to 'builtin_allowlist: extended|core|none'"
            )

        return data
