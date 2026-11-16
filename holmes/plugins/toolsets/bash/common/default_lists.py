"""
Default allow/deny lists for bash toolset.

Two tiers of default allow lists:
- CORE_ALLOW_LIST: read-only commands safe on the CLI and in containers —
  kubectl read-only verbs, JSON/text processing, and system info. Mostly used on
  piped input, though a few also read a file when given a path; none modify state.
- EXTENDED_ALLOW_LIST: adds filesystem commands (cat, find, ls, etc.) — safe in
  containers, but able to expose sensitive files on a local machine (~/.ssh,
  ~/.aws, etc.).

Argument-level primitives that would turn these commands into arbitrary code
execution, file writes, or deletion (e.g. `find -exec`, `sort --compress-program`,
output redirection) are blocked separately by the argv-aware checks in
validation.py, independent of allow-list membership.

Controlled by `builtin_allowlist` config field:
- "core" (CLI default): Uses CORE_ALLOW_LIST
- "extended" (Helm default): Uses EXTENDED_ALLOW_LIST
- "none": Empty allow list, user manages their own
"""

from typing import List

# Core allow list — read-only commands safe on the CLI and in containers.
# See the module docstring for the file-read caveat.
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
    # Prints arguments to stdout — does not read files
    "echo",
]

# Extended allow list - adds filesystem access commands
# Safe in containerized environments with minimal filesystems, but can expose
# sensitive files on local machines (~/.ssh, ~/.aws, /etc/shadow, etc.)
EXTENDED_ALLOW_LIST: List[str] = CORE_ALLOW_LIST + [
    # File reading
    "cat",
    "base64",
    # Filesystem traversal
    "ls",
    "find",
    "stat",
    "du",
    "df",
]

# Default deny list - commands that should require explicit approval
DEFAULT_DENY_LIST: List[str] = []
