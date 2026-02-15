"""
Default allow/deny lists for bash toolset.

Two tiers of default allow lists:
- CORE_ALLOW_LIST: Safe everywhere (CLI and containers). Includes kubectl read-only
  commands, JSON processing, text filtering, and system info. Does NOT include
  commands that can read arbitrary files from the local filesystem.
- EXTENDED_ALLOW_LIST: Adds filesystem access commands (cat, find, ls, etc.) that are
  safe in containerized environments with minimal filesystems, but could expose
  sensitive files on local machines (~/.ssh, ~/.aws, etc.).

Controlled by `builtin_allowlist` config field:
- "core" (CLI default): Uses CORE_ALLOW_LIST
- "extended" (Helm default): Uses EXTENDED_ALLOW_LIST
- "none": Empty allow list, user manages their own
"""

from typing import List

# Core allow list - safe everywhere (CLI and containerized)
# These commands are read-only and don't access the local filesystem
CORE_ALLOW_LIST: List[str] = [
    # Kubernetes read-only commands (RBAC-limited regardless of environment)
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
    # JSON processing
    "jq",
    # Text filtering (operates on stdin/piped data)
    "grep",
    "head",
    "tail",
    "sort",
    "uniq",
    "wc",
    "cut",
    "tr",
    # Process/system info (benign)
    "id",
    "whoami",
    "hostname",
    "uname",
    "date",
    "which",
    "type",
]

# Extended allow list - adds filesystem access commands
# Safe in containerized environments with minimal filesystems, but can expose
# sensitive files on local machines (~/.ssh, ~/.aws, /etc/shadow, etc.)
EXTENDED_ALLOW_LIST: List[str] = CORE_ALLOW_LIST + [
    # File reading
    "cat",
    "echo",
    "base64",
    # Filesystem traversal
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
]

# Default deny list - commands that should require explicit approval
DEFAULT_DENY_LIST: List[str] = []
