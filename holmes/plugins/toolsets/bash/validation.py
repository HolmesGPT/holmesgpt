"""
Prefix-based command validation for the bash toolset.

This module provides validation logic for bash commands using prefix matching
against allow/deny lists, with support for composed commands (pipes, &&, etc.).
"""

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import bashlex
from bashlex import ast

from holmes.common.env_vars import HOLMES_TOOL_RESULT_STORAGE_PATH, load_bool

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


# ---------------------------------------------------------------------------
# Argument-level (argv) security checks.
#
# Prefix matching validates only a command's *name*. Some allow-listed commands
# accept arguments (or shell redirections) that turn a read-only tool into
# arbitrary code execution, file writes, or deletion. These checks inspect the
# parsed argv/redirections and DENY those primitives regardless of allow-list
# membership or prior approval, so they are never auto-executed. (This operates
# on the parsed AST; commands bashlex cannot parse are routed to approval by
# validate_command and so are never auto-executed either.)
#
# Scope note: `tar`/`zcat`/`zgrep`/`gzip` are intentionally NOT in the builtin
# allow lists (see default_lists.py); any use of them already requires approval,
# so they need no argv rule here.
# ---------------------------------------------------------------------------

# `find` action primitives that execute commands or write/delete files. `find`
# uses word-style primaries (no getopt clustering), so exact-token matching is
# correct here.
FIND_DANGEROUS_PRIMITIVES = frozenset(
    {
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",  # execute a command
        "-delete",  # delete matched files
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",  # write to an arbitrary file
    }
)

# getopt-style value-taking options, used to parse short-option clusters
# correctly (so, e.g., the `o` in `sort -to` is read as `-t`'s value, not `-o`,
# and the `2` in `uniq -f 2 in` is not counted as a positional).
SORT_VALUE_SHORT_CHARS = frozenset("ktSTo")
SORT_VALUE_LONG_OPTS = frozenset(
    {
        "--output",
        "--compress-program",
        "--buffer-size",
        "--key",
        "--field-separator",
        "--temporary-directory",
        "--batch-size",
        "--files0-from",
        "--random-source",
    }
)
# `sort` options that write a file, or execute a program on spill. Long options
# are matched by prefix-abbreviation (GNU getopt_long accepts `--out` for
# `--output`); `-o` is the short output option.
SORT_WRITE_LONG_OPTS = frozenset({"--output"})
SORT_EXEC_LONG_OPTS = frozenset({"--compress-program"})

UNIQ_VALUE_SHORT_CHARS = frozenset("fsw")
UNIQ_VALUE_LONG_OPTS = frozenset({"--skip-fields", "--skip-chars", "--check-chars"})

# Redirection / output targets that are not real files (writing to them is
# benign): the null sink, the standard streams, the terminal, and fd aliases.
BENIGN_REDIRECT_TARGETS = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
)


def _is_benign_redirect_target(target: str) -> bool:
    """A redirect/output target that is not a real file on disk."""
    return target in BENIGN_REDIRECT_TARGETS or target.startswith("/dev/fd/")


# bashlex word sub-part kinds that expand at shell runtime into text the static
# argv check cannot see: `$(...)`/backticks, `$VAR`/`${VAR}`, and `<(...)`.
# Detecting these from the AST (not the dequoted string) means a *quoted* literal
# like '*$(x)*' — which the shell never expands — is correctly treated as inert.
DYNAMIC_WORD_PART_KINDS = frozenset(
    {"parameter", "commandsubstitution", "processsubstitution"}
)


def _word_node_has_dynamic_expansion(word_node) -> bool:
    """True if a bashlex WordNode contains a runtime expansion sub-part."""
    return any(
        getattr(part, "kind", None) in DYNAMIC_WORD_PART_KINDS
        for part in (getattr(word_node, "parts", None) or [])
    )


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
    DANGEROUS_ARGUMENT = "dangerous_argument"


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

    # Auto-allow read-only commands for the tool result storage directory so the
    # LLM can access saved large tool results without approval prompts.
    tool_result_prefixes: List[str] = []
    if load_bool("HOLMES_TOOL_RESULT_STORAGE_ENABLED", True):
        storage_path = HOLMES_TOOL_RESULT_STORAGE_PATH
        tool_result_prefixes = [
            f"cat {storage_path}",
            f"head {storage_path}",
            f"tail {storage_path}",
            f"wc {storage_path}",
            f"jq {storage_path}",
        ]

    allow_list = sorted(set(builtin + config.allow + tool_result_prefixes))
    deny_list = sorted(set(DEFAULT_DENY_LIST + config.deny))

    return allow_list, deny_list


class CommandSegmentExtractor(ast.nodevisitor):
    """
    Bashlex AST visitor that extracts command segments.

    Sets contains_compound_command flag when compound statements are encountered,
    but continues traversal to extract inner command segments.

    Also collects, for argv-level security checks:
    - command_argvs: the word list (argv) of every simple command node
    - write_redirect_targets: targets of output redirections to a real file
    """

    def __init__(self, command: str):
        self.command = command
        self.segments: List[str] = []
        self.contains_compound_command: bool = False
        self.command_argvs: List[List[str]] = []
        # Parallel to command_argvs: True if any *argument* (not argv[0]) contains
        # a runtime expansion, so the parsed argv may differ from what the shell runs.
        self.command_arg_dynamic: List[bool] = []
        self.write_redirect_targets: List[str] = []

    def visitcommand(self, node, *args, **kwargs):
        """Extract the command text for simple commands."""
        cmd_text = self.command[node.pos[0] : node.pos[1]].strip()
        self.segments.append(cmd_text)
        word_parts = [part for part in node.parts if getattr(part, "kind", None) == "word"]
        if word_parts:
            self.command_argvs.append([part.word for part in word_parts])
            self.command_arg_dynamic.append(
                any(_word_node_has_dynamic_expansion(part) for part in word_parts[1:])
            )

    def visitcompound(self, node, *args, **kwargs):
        """Flag compound statements but continue traversal to extract inner segments."""
        self.contains_compound_command = True

    def visitredirect(self, node, input, type, output, heredoc):  # noqa: A002
        """Record output redirections whose target is a real file.

        A redirection writes to a file when its type contains '>' and its output
        is a word (a path) rather than an integer (an fd, e.g. `2>&1`). Writes to
        /dev/null, /dev/stdout and /dev/stderr are benign and ignored.
        """
        if type and ">" in type and hasattr(output, "word"):
            target = output.word
            if not _is_benign_redirect_target(target):
                self.write_redirect_targets.append(target)


def _build_extractor(command: str) -> CommandSegmentExtractor:
    """Parse a command and run the segment/argv/redirect extractor over it.

    Raises:
        bashlex.errors.ParsingError: If bashlex cannot parse the command
        NotImplementedError: If bashlex encounters unsupported syntax (e.g. case statements)
    """
    extractor = CommandSegmentExtractor(command)
    for part in bashlex.parse(command):
        extractor.visit(part)
    return extractor


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
    extractor = _build_extractor(command)
    return (extractor.segments, extractor.contains_compound_command)


def _abbreviates(opt: str, targets: frozenset) -> bool:
    """True if `opt` (e.g. '--out') is a non-empty prefix-abbreviation of any long
    option in `targets`. GNU getopt_long accepts unambiguous abbreviations, so a
    security check must treat `--out` as `--output`. Errs toward matching (safe):
    an ambiguous abbreviation the real tool would reject is still flagged."""
    return len(opt) > 2 and opt.startswith("--") and any(t.startswith(opt) for t in targets)


def _parse_argv(
    args: List[str],
    value_short_chars: frozenset,
    value_long_opts: frozenset,
) -> Tuple[set, List[str]]:
    """Minimal getopt-style parse of a command's arguments.

    Models the two things a name-only check misses: short-option clustering
    (`-ro` == `-r -o`) and options that consume the following token as their
    value (`-f 2`, `--skip-fields 2`). It is only precise enough to tell an
    option from a positional and to know which option letters are present — not
    a full getopt implementation.

    Args:
        value_short_chars: single letters whose short option takes a value.
        value_long_opts: `--name` long options that take a value as a separate token.

    Returns:
        (options_present, positionals) where options_present holds tokens like
        '-o' / '--output' and positionals holds the non-option arguments.
    """
    options: set = set()
    positionals: List[str] = []
    i = 0
    end_of_options = False
    while i < len(args):
        arg = args[i]
        if end_of_options or arg == "-" or not arg.startswith("-"):
            positionals.append(arg)  # '-' (stdin) counts as a positional
            i += 1
            continue
        if arg == "--":
            end_of_options = True
            i += 1
            continue
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            options.add(name)
            # A required-value long option consumes the next token unless the
            # value was given inline as --name=value. Match by abbreviation.
            i += 2 if ("=" not in arg and _abbreviates(name, value_long_opts)) else 1
            continue
        # Short-option cluster, e.g. -c, -cf, -ro, -ofile.
        consumes_next = False
        for pos in range(1, len(arg)):
            options.add("-" + arg[pos])
            if arg[pos] in value_short_chars:
                # The value is the rest of this token if present, else the next
                # token. Either way the cluster ends here.
                consumes_next = pos == len(arg) - 1
                break
        i += 2 if consumes_next else 1
    return options, positionals


# --- Per-command argv checkers -------------------------------------------------
# Each takes a command's arguments (argv without argv[0]) and returns a
# human-readable reason if it uses a code-exec/write/delete primitive, else None.
# One command per function so each rule can be reviewed and unit-tested in
# isolation; they are dispatched by basename via _ARGV_CHECKERS below. To cover a
# new command, add a checker and one registry entry (and a case to the allow-list
# guard test).


def _find_dangerous_reason(args: List[str]) -> Optional[str]:
    """`find` action primitives (-exec/-delete/-fprint…) run commands or write files."""
    for arg in args:
        if arg in FIND_DANGEROUS_PRIMITIVES:
            return f"'find' argument '{arg}' can execute commands or write/delete files"
    return None


def _sort_dangerous_reason(args: List[str]) -> Optional[str]:
    """`sort --compress-program` runs a program; `sort -o`/`--output` writes a file."""
    options, _ = _parse_argv(args, SORT_VALUE_SHORT_CHARS, SORT_VALUE_LONG_OPTS)
    long_opts = [opt for opt in options if opt.startswith("--")]
    if any(_abbreviates(opt, SORT_EXEC_LONG_OPTS) for opt in long_opts):
        return "'sort --compress-program' can execute an arbitrary program"
    if "-o" in options or any(_abbreviates(opt, SORT_WRITE_LONG_OPTS) for opt in long_opts):
        return "'sort' output-file option writes to the filesystem"
    return None


def _uniq_dangerous_reason(args: List[str]) -> Optional[str]:
    """`uniq [OPTION]... [INPUT [OUTPUT]]` — a 2nd positional is an output file,
    unless it is a standard stream / '-' (stdout), which is not a real file."""
    positionals = _uniq_positional_args(args)
    if len(positionals) >= 2 and not (
        positionals[1] == "-" or _is_benign_redirect_target(positionals[1])
    ):
        return "'uniq' output-file argument writes to the filesystem"
    return None


def _uniq_positional_args(args: List[str]) -> List[str]:
    """Return the positional (non-option) arguments of a `uniq` invocation."""
    _, positionals = _parse_argv(args, UNIQ_VALUE_SHORT_CHARS, UNIQ_VALUE_LONG_OPTS)
    return positionals


# Command basename -> checker. Only these commands have argv-level rules.
_ARGV_CHECKERS = {
    "find": _find_dangerous_reason,
    "sort": _sort_dangerous_reason,
    "uniq": _uniq_dangerous_reason,
}


def _dangerous_argv_reason(argv: List[str]) -> Optional[str]:
    """Return a human-readable reason if argv uses a code-exec/write/delete
    primitive, else None. Dispatch is scoped to the command's basename so, e.g.,
    `sort -o` is caught but `kubectl get -o wide` is not."""
    if not argv:
        return None
    checker = _ARGV_CHECKERS.get(os.path.basename(argv[0]))
    return checker(argv[1:]) if checker else None


def _unsafe_arg_result(reason: str, approval_mode: bool) -> ValidationResult:
    """Block an exec/write vector: DENIED by default, or (in approval mode)
    APPROVAL_REQUIRED so a human can allow it. Either way it never auto-executes."""
    if approval_mode:
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message=(
                f"Command requires approval: {reason}. The bash toolset is "
                "read-only, so this is not auto-executed."
            ),
            prefixes_needing_approval=[],
        )
    return ValidationResult(
        status=ValidationStatus.DENIED,
        deny_reason=DenyReason.DANGEROUS_ARGUMENT,
        message=(
            f"Command blocked for security reasons: {reason}. The bash toolset "
            "is read-only; this is not auto-executed."
        ),
    )


def check_dangerous_argv(extractor: CommandSegmentExtractor) -> Optional[ValidationResult]:
    """Argv-level and redirection security checks that prefix matching cannot see.

    Returns:
        - DENIED if any command segment uses a dangerous argument primitive or
          writes to a real file via output redirection (checked first, so a hard
          deny is never downgraded to approval);
        - APPROVAL_REQUIRED if an argv-checked command (find/sort/uniq) builds an
          argument via shell expansion, whose runtime value we cannot inspect
          statically and which could smuggle a blocked primitive;
        - None otherwise.

    This inspects the parsed AST, so it applies to commands bashlex can parse.
    Commands bashlex cannot parse never reach here — validate_command routes them
    to APPROVAL_REQUIRED (human in the loop), so they are never auto-executed.

    How the exec/write vectors are handled is set by HOLMES_BASH_UNSAFE_ARGS_MODE:
      - "deny" (default): block them outright (they are never auto-executed and
        cannot be approved);
      - "approval": still not auto-executed, but a human may approve a genuinely
        read-only use (e.g. `find … -exec grep …`).
    Any other value falls back to "deny". This does NOT auto-run anything either
    way — it only chooses between blocking and prompting a human.
    """
    # Unknown/empty values fail safe to the strict "deny" behaviour.
    approval_mode = (
        os.environ.get("HOLMES_BASH_UNSAFE_ARGS_MODE", "deny").strip().lower()
        == "approval"
    )

    # DENY (or, in approval mode, gate) checks first, across ALL segments, so a
    # hard block is never downgraded by an earlier segment that merely contains a
    # shell expansion.
    for argv in extractor.command_argvs:
        reason = _dangerous_argv_reason(argv)
        if reason:
            return _unsafe_arg_result(reason, approval_mode)

    if extractor.write_redirect_targets:
        target = extractor.write_redirect_targets[0]
        return _unsafe_arg_result(
            f"output redirection to '{target}' writes to the filesystem",
            approval_mode,
        )

    # No hard deny. A runtime expansion ($(...), `...`, $VAR/${VAR}, <(...)) in an
    # argv-checked command's arguments can expand into a blocked primitive that the
    # static checks above cannot see, so require explicit approval rather than
    # auto-allowing it.
    for argv, arg_is_dynamic in zip(
        extractor.command_argvs, extractor.command_arg_dynamic
    ):
        if arg_is_dynamic and os.path.basename(argv[0]) in _ARGV_CHECKERS:
            return ValidationResult(
                status=ValidationStatus.APPROVAL_REQUIRED,
                message=(
                    f"'{os.path.basename(argv[0])}' builds an argument via shell "
                    "expansion, which cannot be verified as read-only and requires "
                    "approval."
                ),
                prefixes_needing_approval=[],
            )

    return None


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
        extractor = _build_extractor(command)
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

    segments = extractor.segments
    contains_compound_command = extractor.contains_compound_command

    # Argv-level security check: deny code-exec/write primitives and output
    # redirections that prefix matching cannot see. Non-overridable, so it runs
    # before the allow-list check below (and applies to every command node,
    # including those inside pipes/compound statements).
    dangerous = check_dangerous_argv(extractor)
    if dangerous:
        return dangerous

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
