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
    "kubectl config view",
    "kubectl config current-context",
    "kubectl cluster-info",
    "kubectl version",
    "kubectl auth can-i",
    "kubectl diff",
    "kubectl events",
    # Helm read-only commands
    "helm list",
    "helm status",
    "helm get",
    "helm history",
    "helm show",
    "helm search",
    "helm version",
    "helm repo list",
    # Kube-lineage
    "kube-lineage",
    # JSON processing
    "jq",
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
    # Archive inspection
    "tar -tf",
    "tar -tvf",
    "gzip -l",
    "zcat",
    "zgrep",
    # System monitoring
    "ps",
    "top -b",
    "free",
    "uptime",
    # Process/system info
    "id",
    "whoami",
    "hostname",
    "uname",
    "date",
    "which",
    "type",
]

# Default deny list (used when include_default_allow_deny_list=True)
DEFAULT_DENY_LIST: List[str] = []

# Hardcoded blocks - these patterns are ALWAYS blocked and cannot be overridden
HARDCODED_BLOCKS = [
    "sudo",
    "su",
    ":(){",  # Fork bomb pattern
]
