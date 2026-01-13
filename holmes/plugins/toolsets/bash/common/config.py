from typing import List

from pydantic import BaseModel

# Hardcoded blocks - these patterns are ALWAYS blocked and cannot be overridden
HARDCODED_BLOCKS: List[str] = [
    "sudo",
    "su",
]


class KubectlImageConfig(BaseModel):
    image: str
    allowed_commands: list[str]


class KubectlConfig(BaseModel):
    allowed_images: list[KubectlImageConfig] = []


class BashExecutorConfig(BaseModel):
    """Configuration for the bash toolset with prefix-based validation."""

    # Allow/deny lists for prefix-based command validation
    allow: List[str] = []
    deny: List[str] = []

    # When True, merges user lists with default allow/deny lists
    # Default: False for CLI (user builds trusted commands over time)
    # Should be True for server/in-cluster deployments
    include_default_allow_deny_list: bool = False

    # Legacy config for kubectl run image command
    kubectl: KubectlConfig = KubectlConfig()
