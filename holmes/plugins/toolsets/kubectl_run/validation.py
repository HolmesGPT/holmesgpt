"""Validation logic for kubectl run commands."""

import re
from typing import Optional

from holmes.plugins.toolsets.kubectl_run.config import (
    KubectlImageConfig,
    KubectlRunConfig,
)

# Shell control characters and command-combination sequences that must never
# appear in a container command.
#
# The command is executed as an argv list with shell=False, so these are already
# inert as far as the Holmes host is concerned. Rejecting them up front is
# defense-in-depth: it keeps a loosely-written operator allowlist (e.g. a pattern
# ending in ``.*``) from ever mattering, protects against any future refactor that
# reintroduces a shell, and gives the LLM a clear error instead of a silently
# mangled argument.
#
# The set is scoped to characters/sequences that signal command *combination* or
# *substitution* and have no legitimate place in a single debug command's
# arguments: separators/pipes (``;`` ``|``), redirection (``<`` ``>``), newlines,
# chaining (``&&``) and command/parameter substitution (``` ``` ``` ``$(`` ``${``).
# Characters that are harmless when passed as literal arguments — notably a lone
# ``&`` (URL query strings such as ``?a=1&b=2``) or ``$`` — are intentionally left
# alone so the documented curl/wget use cases keep working; the argv boundary is
# what actually prevents them from being interpreted.
FORBIDDEN_COMMAND_CHARACTERS = [
    ";",
    "|",
    "`",
    "<",
    ">",
    "\n",
    "\r",
]
FORBIDDEN_COMMAND_SEQUENCES = [
    "$(",
    "${",
    "&&",
]


def _reject_shell_metacharacters(container_command: str) -> None:
    """Raise ValueError if the command contains a forbidden shell control token."""
    found = [repr(c) for c in FORBIDDEN_COMMAND_CHARACTERS if c in container_command]
    found += [
        repr(seq) for seq in FORBIDDEN_COMMAND_SEQUENCES if seq in container_command
    ]
    if found:
        raise ValueError(
            f"Command '{container_command}' is not allowed: it contains disallowed "
            f"shell control token(s): {', '.join(found)}. kubectl-run commands run "
            f"directly without a shell, so shell operators (pipes, redirects, command "
            f"substitution, chaining) are never interpreted and are rejected."
        )


def validate_image_and_commands(
    image: str, container_command: str, config: Optional[KubectlRunConfig]
) -> None:
    """
    Validate that the image is in the whitelist and commands are allowed.
    Raises ValueError if validation fails.
    """
    if not config or not config.allowed_images:
        raise ValueError(
            "The command `kubectl run` is not allowed. The user must whitelist specific images and commands but none have been configured."
        )

    # Find matching image config
    image_config: Optional[KubectlImageConfig] = None
    for img_config in config.allowed_images:
        if img_config.image == image:
            image_config = img_config
            break

    if not image_config:
        allowed_images = [img.image for img in config.allowed_images]
        raise ValueError(
            f"Image '{image}' not allowed. Allowed images: {', '.join(allowed_images)}"
        )

    # Reject shell control characters regardless of the operator's regex. This is the
    # primary guard against command injection into the Holmes host.
    _reject_shell_metacharacters(container_command)

    # Validate commands against allowed patterns. Use fullmatch (end-anchored) so a
    # pattern bounds the *entire* command, not just its prefix — re.match would let
    # a matching prefix be followed by arbitrary trailing content.
    command_allowed = False
    for allowed_pattern in image_config.allowed_commands:
        if re.fullmatch(allowed_pattern, container_command):
            command_allowed = True
            break

    if not command_allowed:
        raise ValueError(
            f"Command '{container_command}' not allowed for image '{image}'. "
            f"Allowed patterns: {', '.join(image_config.allowed_commands)}"
        )
