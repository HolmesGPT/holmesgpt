"""Per-command argument (argv) danger rules for the bash toolset.

Prefix allow-listing validates only a command's *name*. A few allow-listed
commands accept arguments that turn a read-only tool into arbitrary code
execution, file writes, or deletion. Each such command gets ONE checker here: it
takes the command's arguments (argv without argv[0]) and returns a human-readable
reason, else None. `validation.py` turns a returned reason into a DENY/APPROVAL
verdict; generic argv parsing lives in argv_utils.py.

To cover a new command:
  1. add a `_<cmd>_reason(args)` checker below, and
  2. register it in `_ARGV_CHECKERS`.
(Also add a case to the allow-list guard test.)

Scope note: `tar`/`zcat`/`zgrep`/`gzip` are intentionally NOT in the builtin
allow lists (see default_lists.py); any use of them already requires approval,
so they need no rule here.
"""

import os
from typing import List, Optional

from holmes.plugins.toolsets.bash.argv_utils import (
    abbreviates,
    is_benign_redirect_target,
    parse_argv,
)

# --- find ---------------------------------------------------------------------
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


def _find_reason(args: List[str]) -> Optional[str]:
    """`find` action primitives (-exec/-delete/-fprint…) run commands or write files."""
    for arg in args:
        if arg in FIND_DANGEROUS_PRIMITIVES:
            return f"'find' argument '{arg}' can execute commands or write/delete files"
    return None


# --- sort ---------------------------------------------------------------------
# Value-taking options, so short-option clusters parse correctly (the `o` in
# `sort -to` is `-t`'s value, not `-o`).
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
# Options that write a file, or execute a program on spill. Long options are
# matched by prefix-abbreviation (GNU getopt_long accepts `--out` for `--output`);
# `-o` is the short output option.
SORT_WRITE_LONG_OPTS = frozenset({"--output"})
SORT_EXEC_LONG_OPTS = frozenset({"--compress-program"})


def _sort_reason(args: List[str]) -> Optional[str]:
    """`sort --compress-program` runs a program; `sort -o`/`--output` writes a file."""
    options, _ = parse_argv(args, SORT_VALUE_SHORT_CHARS, SORT_VALUE_LONG_OPTS)
    long_opts = [opt for opt in options if opt.startswith("--")]
    if any(abbreviates(opt, SORT_EXEC_LONG_OPTS) for opt in long_opts):
        return "'sort --compress-program' can execute an arbitrary program"
    if "-o" in options or any(abbreviates(opt, SORT_WRITE_LONG_OPTS) for opt in long_opts):
        return "'sort' output-file option writes to the filesystem"
    return None


# --- uniq ---------------------------------------------------------------------
UNIQ_VALUE_SHORT_CHARS = frozenset("fsw")
UNIQ_VALUE_LONG_OPTS = frozenset({"--skip-fields", "--skip-chars", "--check-chars"})


def _uniq_positional_args(args: List[str]) -> List[str]:
    """Return the positional (non-option) arguments of a `uniq` invocation."""
    _, positionals = parse_argv(args, UNIQ_VALUE_SHORT_CHARS, UNIQ_VALUE_LONG_OPTS)
    return positionals


def _uniq_reason(args: List[str]) -> Optional[str]:
    """`uniq [OPTION]... [INPUT [OUTPUT]]` — a 2nd positional is an output file,
    unless it is a standard stream / '-' (stdout), which is not a real file."""
    positionals = _uniq_positional_args(args)
    if len(positionals) >= 2 and not (
        positionals[1] == "-" or is_benign_redirect_target(positionals[1])
    ):
        return "'uniq' output-file argument writes to the filesystem"
    return None


# --- dispatch -----------------------------------------------------------------
# Command basename -> checker. Private: callers use the functions below rather
# than reaching into the registry.
_ARGV_CHECKERS = {
    "find": _find_reason,
    "sort": _sort_reason,
    "uniq": _uniq_reason,
}


def dangerous_argv_reason(argv: List[str]) -> Optional[str]:
    """Return a human-readable reason if argv uses a code-exec/write/delete
    primitive, else None. Dispatch is scoped to the command's basename so, e.g.,
    `sort -o` is caught but `kubectl get -o wide` is not."""
    if not argv:
        return None
    checker = _ARGV_CHECKERS.get(os.path.basename(argv[0]))
    return checker(argv[1:]) if checker else None


def is_argv_checked_command(name: str) -> bool:
    """True if `name` (a command basename) has per-argument rules here."""
    return name in _ARGV_CHECKERS
