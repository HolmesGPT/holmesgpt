from typing import List

from pydantic import Field

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
        description="Allow list of prefixes for command validation",
    )
    deny: List[str] = Field(
        default_factory=list,
        title="Deny List",
        description="Deny list of prefixes for command validation",
    )

    # When True, merges user lists with default allow/deny lists
    # Default: False for CLI (user builds trusted commands over time)
    # Should be True for server/in-cluster deployments
    include_default_allow_deny_list: bool = Field(
        default=False,
        title="Include Defaults",
        description="Include default allow/deny lists",
    )
