"""Generic argv / redirect-target helpers for the bash toolset.

Pure, command-agnostic utilities shared by the per-command rules in
command_arg_rules.py and by the redirect detection in validation.py. Nothing
here knows about any specific command.
"""

from typing import List, Tuple

# Redirection / output targets that are not real files (writing to them is
# benign): the null sink, the standard streams, the terminal, and fd aliases.
BENIGN_REDIRECT_TARGETS = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
)


def is_benign_redirect_target(target: str) -> bool:
    """A redirect/output target that is not a real file on disk."""
    return target in BENIGN_REDIRECT_TARGETS or target.startswith("/dev/fd/")


def abbreviates(opt: str, targets: frozenset) -> bool:
    """True if `opt` (e.g. '--out') is a non-empty prefix-abbreviation of any long
    option in `targets`. GNU getopt_long accepts unambiguous abbreviations, so a
    security check must treat `--out` as `--output`. Errs toward matching (safe):
    an ambiguous abbreviation the real tool would reject is still flagged."""
    return len(opt) > 2 and opt.startswith("--") and any(t.startswith(opt) for t in targets)


def parse_argv(
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
            i += 2 if ("=" not in arg and abbreviates(name, value_long_opts)) else 1
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
