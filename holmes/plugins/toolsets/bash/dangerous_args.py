"""Argument-level (argv) danger detection for the bash toolset.

Prefix allow-listing validates only a command's *name*. A few allow-listed
commands accept arguments (or output redirections) that turn a read-only tool
into arbitrary code execution, file writes, or deletion. This module inspects a
parsed command's argv and reports such a primitive, per command; `validation.py`
turns a reported reason into a DENY/APPROVAL verdict and owns the AST parsing.

To cover a new command: add a `_<cmd>_dangerous_reason(args)` checker and one
`ARGV_CHECKERS` entry (and a case to the allow-list guard test).

Scope note: `tar`/`zcat`/`zgrep`/`gzip` are intentionally NOT in the builtin
allow lists (see default_lists.py); any use of them already requires approval,
so they need no argv rule here.
"""

import os
from typing import List, Optional, Tuple

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


def is_benign_redirect_target(target: str) -> bool:
    """A redirect/output target that is not a real file on disk."""
    return target in BENIGN_REDIRECT_TARGETS or target.startswith("/dev/fd/")


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
# isolation; they are dispatched by basename via ARGV_CHECKERS below.


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
        positionals[1] == "-" or is_benign_redirect_target(positionals[1])
    ):
        return "'uniq' output-file argument writes to the filesystem"
    return None


def _uniq_positional_args(args: List[str]) -> List[str]:
    """Return the positional (non-option) arguments of a `uniq` invocation."""
    _, positionals = _parse_argv(args, UNIQ_VALUE_SHORT_CHARS, UNIQ_VALUE_LONG_OPTS)
    return positionals


# Command basename -> checker. Only these commands have argv-level rules; the set
# of keys is the single source of truth for "which commands are argv-checked".
ARGV_CHECKERS = {
    "find": _find_dangerous_reason,
    "sort": _sort_dangerous_reason,
    "uniq": _uniq_dangerous_reason,
}


def dangerous_argv_reason(argv: List[str]) -> Optional[str]:
    """Return a human-readable reason if argv uses a code-exec/write/delete
    primitive, else None. Dispatch is scoped to the command's basename so, e.g.,
    `sort -o` is caught but `kubectl get -o wide` is not."""
    if not argv:
        return None
    checker = ARGV_CHECKERS.get(os.path.basename(argv[0]))
    return checker(argv[1:]) if checker else None
