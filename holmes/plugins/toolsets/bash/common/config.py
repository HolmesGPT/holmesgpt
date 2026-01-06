from typing import List

from pydantic import BaseModel


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


# Default allow list (used when include_default_allow_deny_list=True)
DEFAULT_ALLOW_LIST = [
    # Kubernetes read-only commands
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
    "kubectl top",
    "kubectl explain",
    "kubectl api-resources",
    # Text processing
    "cat",
    "grep",
    "head",
    "tail",
    "sort",
    "uniq",
    "wc",
    "cut",
    "tr",
    "echo",
    "base64",
    # File system inspection
    "ls",
    "find",
    "stat",
    "file",
    "du",
    "df",
    # System monitoring
    "ps",
    "top -b",
    "free",
    "uptime",
]

# Default deny list (used when include_default_allow_deny_list=True)
# Note: Plural forms (e.g., 'secrets') and path syntax (e.g., 'secret/name')
# are automatically matched by match_prefix_for_deny()
DEFAULT_DENY_LIST = [
    "kubectl get secret",
    "kubectl describe secret",
]

# Hardcoded blocks - these patterns are ALWAYS blocked and cannot be overridden
HARDCODED_BLOCKS = [
    "sudo",
    "su",
    ":(){",  # Fork bomb pattern
]
