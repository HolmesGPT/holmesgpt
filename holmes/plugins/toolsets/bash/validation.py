"""
Prefix-based command validation for the bash toolset.

This module provides validation logic for bash commands using prefix matching
against allow/deny lists, with support for composed commands (pipes, &&, etc.).
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import bashlex

from holmes.plugins.toolsets.bash.common.config import (
    DEFAULT_ALLOW_LIST,
    DEFAULT_DENY_LIST,
    HARDCODED_BLOCKS,
    BashExecutorConfig,
)

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Result status for command validation."""

    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"


class DenyReason(Enum):
    """Reason why a command was denied."""

    HARDCODED_BLOCK = "hardcoded_block"
    DENY_LIST = "deny_list"
    SUBSHELL_DETECTED = "subshell_detected"
    PARSE_ERROR = "parse_error"
    PREFIX_MISMATCH = "prefix_mismatch"
    PREFIX_COUNT_MISMATCH = "prefix_count_mismatch"


@dataclass
class ValidationResult:
    """Result of command validation."""

    status: ValidationStatus
    deny_reason: Optional[DenyReason] = None
    message: Optional[str] = None
    # Prefixes that need approval (for APPROVAL_REQUIRED status)
    prefixes_needing_approval: Optional[List[str]] = None


def get_effective_lists(config: BashExecutorConfig) -> Tuple[List[str], List[str]]:
    """
    Get the effective allow and deny lists based on configuration.

    Returns:
        Tuple of (allow_list, deny_list)
    """
    if config.include_default_allow_deny_list:
        # Merge user lists with defaults
        allow_list = list(set(DEFAULT_ALLOW_LIST + config.allow))
        deny_list = list(set(DEFAULT_DENY_LIST + config.deny))
    else:
        allow_list = config.allow
        deny_list = config.deny

    return allow_list, deny_list


def detect_subshells(command: str) -> bool:
    """
    Detect if a command contains subshell constructs.

    Blocked patterns:
    - $(...) - command substitution
    - `...` - backtick command substitution
    - <(...) - process substitution (input)
    - >(...) - process substitution (output)

    Returns:
        True if subshells detected, False otherwise
    """
    # Check for $(...) - but not $VAR or ${VAR}
    if re.search(r"\$\([^)]*\)", command):
        return True

    # Check for backticks
    if "`" in command:
        return True

    # Check for process substitution <(...) or >(...)
    if re.search(r"[<>]\([^)]*\)", command):
        return True

    return False


def _extract_command_text(node, command: str) -> str:
    """Extract the original command text for a bashlex node."""
    return command[node.pos[0] : node.pos[1]]


def _parse_with_bashlex(command: str) -> List[str]:
    """
    Parse a command using bashlex to extract command segments.

    Returns:
        List of command segments
    """
    segments: List[str] = []

    try:
        parts = bashlex.parse(command)
    except bashlex.errors.ParsingError as e:
        logger.debug(f"bashlex failed to parse command: {e}")
        # Fall back to regex if bashlex fails
        return _parse_with_regex(command)

    def visit_node(node):
        """Recursively visit nodes to find command segments."""
        if node.kind == "command":
            # Extract the command text
            cmd_text = _extract_command_text(node, command)
            segments.append(cmd_text.strip())
        elif node.kind in ("pipeline", "list", "compound"):
            # Recurse into compound structures
            for part in node.parts:
                visit_node(part)
        elif hasattr(node, "parts"):
            for part in node.parts:
                visit_node(part)

    for part in parts:
        visit_node(part)

    return segments


def _parse_with_regex(command: str) -> List[str]:
    """
    Fallback regex-based command parsing.

    Used when bashlex fails to parse the command.
    """
    # Split by shell operators, preserving the command parts
    segments = re.split(r"\s*(?:\|{1,2}|&&|;|&)\s*", command)
    # Filter empty segments and strip whitespace
    return [seg.strip() for seg in segments if seg.strip()]


def parse_command_segments(command: str) -> List[str]:
    """
    Parse a command into segments separated by |, &&, ||, ;, &.

    Uses bashlex for proper shell parsing, with regex fallback.

    Returns:
        List of command segments
    """
    # Try bashlex first
    segments = _parse_with_bashlex(command)

    # Fall back to regex if bashlex returned no segments
    if not segments:
        segments = _parse_with_regex(command)

    return segments


def check_hardcoded_blocks(command: str) -> Optional[str]:
    """
    Check if command matches any hardcoded block patterns.

    Returns:
        The matched block pattern if found, None otherwise
    """
    command_lower = command.lower()

    for block in HARDCODED_BLOCKS:
        # Check if block pattern appears in command
        if block in command_lower:
            return block

    return None


def match_prefix(segment: str, prefix: str) -> bool:
    """
    Check if a command segment matches a prefix.

    The prefix should match the beginning of the command at word boundaries.
    Accepts whitespace or '/' as valid boundaries (for kubectl resource/name syntax).

    Examples:
        - "kubectl get pods" matches prefix "kubectl get"
        - "kubectl delete pod" does NOT match prefix "kubectl get"
        - "grep -r error" matches prefix "grep"
        - "kubectl get secret/my-secret" matches prefix "kubectl get secret"
    """
    segment = segment.strip()
    prefix = prefix.strip()

    # Command must start with the prefix
    if not segment.startswith(prefix):
        return False

    # If prefix is shorter than segment, the next char must be boundary char or end
    if len(segment) > len(prefix):
        next_char = segment[len(prefix)]
        # Allow whitespace or path separator as boundary
        if not (next_char.isspace() or next_char == "/"):
            return False

    return True


def match_prefix_for_deny(segment: str, prefix: str) -> bool:
    """
    Check if a command segment matches a deny list prefix.

    More aggressive than allow list matching to prevent security bypasses:
    - Treats '/' as a valid boundary (catches 'kubectl get secret/name' syntax)
    - Also matches plural form (prefix + 's') to catch resource type aliases

    Examples:
        - "kubectl get secret/my-secret" matches prefix "kubectl get secret"
        - "kubectl get secrets" matches prefix "kubectl get secret" (plural)
        - "kubectl get secrets/my-secret" matches prefix "kubectl get secret"
    """
    segment = segment.strip()
    prefix = prefix.strip()

    def is_deny_boundary_char(char: str) -> bool:
        """Check if char is a valid boundary for deny matching."""
        return char.isspace() or char == "/"

    def check_at_boundary(seg: str, pref: str) -> bool:
        """Check if segment starts with prefix at a valid boundary."""
        if not seg.startswith(pref):
            return False
        if len(seg) > len(pref):
            if not is_deny_boundary_char(seg[len(pref)]):
                return False
        return True

    # Check exact prefix match
    if check_at_boundary(segment, prefix):
        return True

    # Check plural form (handles 'secret' matching 'secrets')
    if check_at_boundary(segment, prefix + "s"):
        return True

    return False


def validate_prefix_for_segment(
    segment: str, prefix: str, allow_list: List[str], deny_list: List[str]
) -> ValidationResult:
    """
    Validate a single command segment against its suggested prefix.

    Validation order:
    1. Hardcoded blocks -> DENIED
    2. Deny list -> DENIED
    3. Allow list -> ALLOWED
    4. Neither -> APPROVAL_REQUIRED
    """
    # Step 1: Check hardcoded blocks
    blocked = check_hardcoded_blocks(segment)
    if blocked:
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.HARDCODED_BLOCK,
            message=f"Command contains '{blocked}' which is permanently blocked for security reasons and cannot be overridden.",
        )

    # Verify prefix actually matches the segment
    if not match_prefix(segment, prefix):
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.PREFIX_MISMATCH,
            message=f"Suggested prefix '{prefix}' does not match command segment '{segment}'.",
        )

    # Step 2: Check deny list (using stricter matching)
    for deny_prefix in deny_list:
        if match_prefix_for_deny(segment, deny_prefix):
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.DENY_LIST,
                message=f"Command matches deny list pattern '{deny_prefix}'. This command is blocked by configuration.",
            )

    # Step 3: Check allow list (using prefix)
    for allow_prefix in allow_list:
        if match_prefix(segment, allow_prefix):
            return ValidationResult(status=ValidationStatus.ALLOWED)

    # Step 4: Not in any list -> needs approval
    return ValidationResult(
        status=ValidationStatus.APPROVAL_REQUIRED,
        message=f"Command prefix '{prefix}' is not in the allow list.",
        prefixes_needing_approval=[prefix],
    )


def validate_command(
    command: str,
    suggested_prefixes: List[str],
    config: BashExecutorConfig,
) -> ValidationResult:
    """
    Validate a bash command against the allow/deny lists.

    Args:
        command: The full bash command to validate
        suggested_prefixes: AI-provided prefixes (one per command segment)
        config: Bash toolset configuration

    Returns:
        ValidationResult with status and details
    """
    # Check for subshells first
    if detect_subshells(command):
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.SUBSHELL_DETECTED,
            message="Command contains subshell constructs ($(), ``, <(), >()) which are not allowed for security reasons.",
        )

    # Parse command into segments
    segments = parse_command_segments(command)

    if not segments:
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.PARSE_ERROR,
            message="Failed to parse command: no valid command segments found.",
        )

    # Verify prefix count matches segment count
    if len(suggested_prefixes) != len(segments):
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.PREFIX_COUNT_MISMATCH,
            message=f"Number of suggested prefixes ({len(suggested_prefixes)}) does not match number of command segments ({len(segments)}). Each pipe/operator segment needs its own prefix.",
        )

    # Get effective allow/deny lists
    allow_list, deny_list = get_effective_lists(config)

    # Validate each segment
    prefixes_needing_approval: List[str] = []

    for segment, prefix in zip(segments, suggested_prefixes):
        result = validate_prefix_for_segment(segment, prefix, allow_list, deny_list)

        # If any segment is denied, the whole command is denied
        if result.status == ValidationStatus.DENIED:
            return result

        # Collect prefixes needing approval
        if result.status == ValidationStatus.APPROVAL_REQUIRED:
            if result.prefixes_needing_approval:
                prefixes_needing_approval.extend(result.prefixes_needing_approval)

    # If any prefixes need approval, return APPROVAL_REQUIRED
    if prefixes_needing_approval:
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Command not in allow list.",
            prefixes_needing_approval=prefixes_needing_approval,
        )

    # All segments validated and allowed
    return ValidationResult(status=ValidationStatus.ALLOWED)
