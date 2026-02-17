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
from bashlex import ast

from holmes.plugins.toolsets.bash.common.config import (
    HARDCODED_BLOCKS,
    BashExecutorConfig,
)
from holmes.plugins.toolsets.bash.common.default_lists import (
    CORE_ALLOW_LIST,
    DEFAULT_DENY_LIST,
    EXTENDED_ALLOW_LIST,
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
    PREFIX_NOT_IN_COMMAND = "fabricated_prefix"


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

    builtin_allowlist controls which builtin list is merged with user-provided entries:
    - "core": kubectl read-only, jq, grep, text processing, system info
    - "extended": core + filesystem commands (cat, find, ls, base64)
    - "none": only user-provided allow/deny entries

    Returns copies to prevent mutation of the shared config.

    Returns:
        Tuple of (allow_list, deny_list) - always returns copies, never references
    """
    if config.builtin_allowlist == "extended":
        builtin = EXTENDED_ALLOW_LIST
    elif config.builtin_allowlist == "core":
        builtin = CORE_ALLOW_LIST
    else:
        builtin = []

    allow_list = sorted(set(builtin + config.allow))
    deny_list = sorted(set(DEFAULT_DENY_LIST + config.deny))

    return allow_list, deny_list


class CommandSegmentExtractor(ast.nodevisitor):
    """
    Bashlex AST visitor that extracts command segments.

    Sets contains_compound_command flag when compound statements are encountered,
    but continues traversal to extract inner command segments.
    """

    def __init__(self, command: str):
        self.command = command
        self.segments: List[str] = []
        self.contains_compound_command: bool = False

    def visitcommand(self, node, *args, **kwargs):
        """Extract the command text for simple commands."""
        cmd_text = self.command[node.pos[0] : node.pos[1]].strip()
        self.segments.append(cmd_text)

    def visitcompound(self, node, *args, **kwargs):
        """Flag compound statements but continue traversal to extract inner segments."""
        self.contains_compound_command = True


def parse_command_segments(command: str) -> Tuple[List[str], bool]:
    """
    Parse a command into segments separated by |, &&, ||, ;, &.

    Uses bashlex AST visitor for proper shell parsing.

    Returns:
        Tuple of (segments, contains_compound_command):
        - segments: List of command segments extracted from the command
        - contains_compound_command: True if compound statements (for, while, if, etc.) were detected

    Raises:
        bashlex.errors.ParsingError: If bashlex cannot parse the command
        NotImplementedError: If bashlex encounters unsupported syntax (e.g. case statements)
    """
    parts = bashlex.parse(command)
    extractor = CommandSegmentExtractor(command)
    for part in parts:
        extractor.visit(part)
    return (extractor.segments, extractor.contains_compound_command)


def check_hardcoded_blocks(segment: str) -> Optional[str]:
    """
    Check if segment matches any hardcoded block patterns.
    Uses same matching logic as deny list for consistency.

    Args:
        segment: A single command segment (already parsed)

    Returns:
        The matched block pattern if found, None otherwise
    """
    segment_lower = segment.lower()
    for block in HARDCODED_BLOCKS:
        if match_prefix_for_deny(segment_lower, block):
            return block

    return None


def check_blocked_in_raw_command(command: str, blocked_list: List[str]) -> Optional[str]:
    """
    Check for blocked patterns anywhere in a raw command string using word boundaries.

    This is the fallback safety check for when bashlex can't parse the command.
    It scans the entire raw command for any pattern from the given list.

    Args:
        command: The full raw command string (may contain compound statements, subshells, etc.)
        blocked_list: List of command patterns to check for (e.g. HARDCODED_BLOCKS or deny_list)

    Returns:
        The matched pattern if found, None otherwise
    """
    command_lower = command.lower()
    for pattern in blocked_list:
        if re.search(rf"\b{re.escape(pattern.lower())}\b", command_lower):
            return pattern
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


def validate_segment(
    segment: str, allow_list: List[str], deny_list: List[str]
) -> ValidationResult:
    """
    Validate a single command segment against allow/deny lists.

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

    # Step 2: Check deny list (using stricter matching)
    for deny_prefix in deny_list:
        if match_prefix_for_deny(segment, deny_prefix):
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.DENY_LIST,
                message=f"Command matches deny list pattern '{deny_prefix}'. This command is blocked by configuration.",
            )

    # Step 3: Check allow list
    for allow_prefix in allow_list:
        if match_prefix(segment, allow_prefix):
            return ValidationResult(status=ValidationStatus.ALLOWED)

    # Step 4: Not in any list -> needs approval
    return ValidationResult(
        status=ValidationStatus.APPROVAL_REQUIRED,
        message=f"Command segment '{segment}' is not in the allow list.",
    )


def validate_command(
    command: str,
    suggested_prefixes: List[str],
    allow_list: List[str],
    deny_list: List[str],
) -> ValidationResult:
    """
    Validate a bash command against the allow/deny lists.

    Args:
        command: The full bash command to validate
        suggested_prefixes: AI-provided prefixes (one per command segment)
        allow_list: List of allowed command prefixes
        deny_list: List of denied command prefixes

    Returns:
        ValidationResult with status and details
    """
    # Verify all suggested prefixes actually appear in the command
    for prefix in suggested_prefixes:
        if prefix not in command:
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.PREFIX_NOT_IN_COMMAND,
                message=f"Suggested prefix '{prefix}' does not appear in the command.",
            )

    # Parse command into segments and detect compound statements
    try:
        segments, contains_compound_command = parse_command_segments(command)
    except (bashlex.errors.ParsingError, NotImplementedError):
        # Can't parse — do safety checks on raw string, then ask user to approve
        blocked = check_blocked_in_raw_command(command, HARDCODED_BLOCKS)
        if blocked:
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.HARDCODED_BLOCK,
                message=f"Command contains '{blocked}' which is permanently blocked for security reasons and cannot be overridden.",
            )
        denied = check_blocked_in_raw_command(command, deny_list)
        if denied:
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.DENY_LIST,
                message=f"Command matches deny list pattern '{denied}'. This command is blocked by configuration.",
            )
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Command contains complex syntax which requires approval.",
            prefixes_needing_approval=[],
        )

    # Validate each segment against deny/allow lists
    unapproved_segments: List[str] = []

    for segment in segments:
        result = validate_segment(segment, allow_list, deny_list)

        # If any segment is denied, the whole command is denied
        if result.status == ValidationStatus.DENIED:
            return result

        if result.status == ValidationStatus.APPROVAL_REQUIRED:
            unapproved_segments.append(segment)

    # Compound commands always require approval, even if all segments are allowed.
    # Only unapproved-segment approvals save prefixes to the allow list —
    # compound and unparseable approvals are one-time only.
    if contains_compound_command:
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Contains compound statements (for/while/if/etc).",
            prefixes_needing_approval=[],
        )

    if unapproved_segments:
        prefixes_needing_approval = list(
            dict.fromkeys(
                prefix
                for prefix in suggested_prefixes
                if not any(match_prefix(prefix, allowed) for allowed in allow_list)
            )
        )
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message=f"Segment(s) not in allow list: {', '.join(repr(s) for s in unapproved_segments)}",
            prefixes_needing_approval=prefixes_needing_approval,
        )

    # All segments validated and allowed
    return ValidationResult(status=ValidationStatus.ALLOWED)
