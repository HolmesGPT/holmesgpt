"""
Default allow/deny lists for bash toolset.

Two tiers of default allow lists:
- CORE_ALLOW_LIST: Intended to be safe everywhere (CLI and containers). Includes
  kubectl read-only commands, JSON processing, text filtering, and system info.
  These are primarily used on stdin/piped data; note that several (grep, head,
  tail, sort, uniq, wc, cut, jq) can also read a file if given a path argument,
  so this tier is not strictly filesystem-free.
- EXTENDED_ALLOW_LIST: Adds filesystem access commands (cat, find, ls, etc.) that are
  safe in containerized environments with minimal filesystems, but could expose
  sensitive files on local machines (~/.ssh, ~/.aws, etc.).

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

# Core allow list - intended to be safe everywhere (CLI and containerized).
# These commands are read-only (they never modify state). Most operate on
# stdin/piped data, though several also accept a file-path argument (see the
# module docstring); argument-level write/exec primitives are blocked in
# validation.py regardless of tier.
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
#
# Archive/compression tools (tar, gzip, zcat, zgrep) are intentionally NOT
# included: they were unused in practice and carry argument-level code-execution
# risk (e.g. `tar --use-compress-program`/`--checkpoint-action`) or, for the
# zgrep/zcat shell-script wrappers, a history of argument-injection issues.
# Users who need them can add them explicitly via the `allow` config.
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
