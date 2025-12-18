import shlex
import re

SAFE_SHELL_CHARS = frozenset(".-_=/,:")

# POSIX character class pattern for tr command
POSIX_CHAR_CLASS_PATTERN = re.compile(r"^\[:[-\w]+:\]$")


def escape_shell_args(args: list[str]) -> list[str]:
    """
    Escape shell arguments to prevent injection.
    Uses manual quoting with single/double quotes as the primary approach,
    falling back to shlex.quote for complex cases with nested quotes.
    """
    escaped_args = []

    for arg in args:
        # If argument is safe (contains only alphanumeric, hyphens, dots, underscores, equals, slash, comma, colon)
        # no escaping needed
        if arg and all(c.isalnum() or c in SAFE_SHELL_CHARS for c in arg):
            escaped_args.append(arg)
        # If argument starts with -- or - (flag), no escaping needed
        elif arg.startswith("-"):
            escaped_args.append(arg)
        # POSIX character classes for tr command (e.g., [:lower:], [:upper:], [:digit:])
        elif POSIX_CHAR_CLASS_PATTERN.match(arg):
            escaped_args.append("'" + arg + "'")
        # Avoid using shlex in case as it does not handle nested quotes well. e.g. "foo='bar'"
        elif "'" not in arg:
            escaped_args.append("'" + arg + "'")
        elif '"' not in arg:
            escaped_args.append('"' + arg + '"')
        # For everything else, use shlex.quote for proper escaping
        else:
            escaped_args.append(shlex.quote(arg))

    return escaped_args
