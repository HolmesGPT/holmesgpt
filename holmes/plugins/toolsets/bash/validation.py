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
    DEFAULT_ALLOW_LIST,
    DEFAULT_DENY_LIST,
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
    COMPOUND_STATEMENT = "compound_statement"
    PARSE_ERROR = "parse_error"
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

    Returns copies to prevent mutation of the shared config.

    Returns:
        Tuple of (allow_list, deny_list) - always returns copies, never references
    """
    if config.include_default_allow_deny_list:
        # Merge user lists with defaults (creates new lists), sorted for deterministic output
        allow_list = sorted(set(DEFAULT_ALLOW_LIST + config.allow))
        deny_list = sorted(set(DEFAULT_DENY_LIST + config.deny))
    else:
        # Return sorted copies for deterministic output
        allow_list = sorted(config.allow)
        deny_list = sorted(config.deny)

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


class CompoundStatementError(Exception):
    """Raised when a compound statement (for, while, if, etc.) is detected."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f"Compound statement detected: {kind}")


# Keywords that indicate compound statements (checked when bashlex can't parse)
COMPOUND_KEYWORDS = {"for", "while", "until", "if", "case", "select", "function"}
COMPOUND_END_KEYWORDS = {"done", "fi", "esac"}


def _detect_compound_keywords(command: str) -> Optional[str]:
    """
    Detect compound statement keywords in a command string.

    This is a fallback check for when bashlex can't parse the command
    (e.g., case statements which bashlex doesn't fully support).

    Returns:
        The detected keyword if found, None otherwise
    """
    words = re.findall(r"\b(\w+)\b", command)
    for word in words:
        if word in COMPOUND_KEYWORDS or word in COMPOUND_END_KEYWORDS:
            return word
    return None


class CommandSegmentExtractor(ast.nodevisitor):
    """
    Bashlex AST visitor that extracts command segments.

    Raises CompoundStatementError when compound statements are encountered.
    """

    def __init__(self, command: str):
        self.command = command
        self.segments: List[str] = []

    def visitcommand(self, node, *args, **kwargs):
        """Extract the command text for simple commands."""
        cmd_text = self.command[node.pos[0] : node.pos[1]].strip()
        self.segments.append(cmd_text)

    def visitcompound(self, node, *args, **kwargs):
        """Reject compound statements (for, while, if, case, etc.)."""
        raise CompoundStatementError(node.kind)


def parse_command_segments(command: str) -> List[str]:
    """
    Parse a command into segments separated by |, &&, ||, ;, &.

    Uses bashlex AST visitor for proper shell parsing.

    Returns:
        List of command segments

    Raises:
        CompoundStatementError: If command contains compound statements
    """
    try:
        parts = bashlex.parse(command)
    except (bashlex.errors.ParsingError, NotImplementedError) as e:
        logger.debug(f"bashlex failed to parse command: {e}")
        # Check for compound keywords when bashlex can't parse
        # (catches cases like `case` statements that bashlex doesn't fully support)
        keyword = _detect_compound_keywords(command)
        if keyword:
            raise CompoundStatementError(keyword)
        # If no compound keywords found, re-raise the parse error
        raise CompoundStatementError(f"parse_error: {e}")

    extractor = CommandSegmentExtractor(command)
    for part in parts:
        extractor.visit(part)

    return extractor.segments


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

    # Check for subshells
    if detect_subshells(command):
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.SUBSHELL_DETECTED,
            message="Command contains subshell constructs ($(), ``, <(), >()) which are not allowed for security reasons.",
        )

    # Parse command into segments (may raise CompoundStatementError)
    try:
        segments = parse_command_segments(command)
    except CompoundStatementError:
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.COMPOUND_STATEMENT,
            message="Compound statements (for, while, if, case, etc.) are not supported. Only simple one-liner commands are allowed.",
        )

    if not segments:
        return ValidationResult(
            status=ValidationStatus.DENIED,
            deny_reason=DenyReason.PARSE_ERROR,
            message="Failed to parse command: no valid command segments found.",
        )

    # Validate each segment
    any_needs_approval = False

    for segment in segments:
        result = validate_segment(segment, allow_list, deny_list)

        # If any segment is denied, the whole command is denied
        if result.status == ValidationStatus.DENIED:
            return result

        if result.status == ValidationStatus.APPROVAL_REQUIRED:
            any_needs_approval = True

    # If any segments need approval, filter suggested_prefixes to only those not already allowed
    # Use dict.fromkeys to deduplicate while preserving order
    if any_needs_approval:
        prefixes_needing_approval = list(
            dict.fromkeys(
                prefix
                for prefix in suggested_prefixes
                if not any(match_prefix(prefix, allowed) for allowed in allow_list)
            )
        )
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Command not in allow list.",
            prefixes_needing_approval=prefixes_needing_approval or suggested_prefixes,
        )

    # All segments validated and allowed
    return ValidationResult(status=ValidationStatus.ALLOWED)
