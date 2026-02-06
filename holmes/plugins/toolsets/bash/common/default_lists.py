"""
Default allow/deny lists for bash toolset.

These lists are used when `include_default_allow_deny_list: true` is set in config.
Typically enabled for server/in-cluster deployments.

For local CLI usage, these lists are empty by default - users build their
trusted command set over time via approval prompts.
"""

from typing import List

# Default allow list - safe read-only commands
DEFAULT_ALLOW_LIST: List[str] = [
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
    "du",
    "df",
    # Archive inspection
    "tar -tf",
    "tar -tvf",
    "tar -tfv",
    "tar -ftv",
    "gzip -l",
    "zcat",
    "zgrep",
    # Process/system info
    "id",
    "whoami",
    "hostname",
    "uname",
    "date",
    "which",
    "type",
]

# Default deny list - commands that should require explicit approval
DEFAULT_DENY_LIST: List[str] = []
