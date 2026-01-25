from typing import List

from pydantic import BaseModel, Field

# Hardcoded blocks - these patterns are ALWAYS blocked and cannot be overridden
HARDCODED_BLOCKS: List[str] = [
    "sudo",
    "su",
]


class BashExecutorConfig(BaseModel):
    """Configuration for the bash toolset with prefix-based validation."""

    # Allow/deny lists for prefix-based command validation
    allow: List[str] = Field(
        default_factory=list, 
        description="Allow list of prefixes for command validation",
    )
    deny: List[str] = Field(
        default_factory=list,
        description="Deny list of prefixes for command validation",
    )

    # When True, merges user lists with default allow/deny lists
    # Default: False for CLI (user builds trusted commands over time)
    # Should be True for server/in-cluster deployments
    include_default_allow_deny_list: bool = Field(
        default=False,
        description="Include default allow/deny lists",
        )
