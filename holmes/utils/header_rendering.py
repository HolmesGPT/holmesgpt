"""Shared utility for rendering Jinja2 template headers with request context.

Used by MCP toolsets, HTTP toolsets, Python toolsets, and YAML toolsets
to propagate HTTP headers from incoming requests to outgoing API calls.
"""

import logging
import os
from typing import Any, Dict, Optional

from jinja2 import Template
from requests.structures import CaseInsensitiveDict

logger = logging.getLogger(__name__)


def render_header_templates(
    extra_headers: Dict[str, str],
    request_context: Optional[Dict[str, Any]] = None,
    source_name: str = "unknown",
) -> Dict[str, str]:
    """Render a dictionary of Jinja2 template headers with request context and env vars.

    Args:
        extra_headers: Dict mapping header names to Jinja2 template strings.
            Templates can reference:
            - {{ request_context.headers['Header-Name'] }} for pass-through headers
            - {{ env.ENV_VAR_NAME }} for environment variables
            - {{ read_file('/path/to/file') }} for a file's contents, read on every render
            - Plain strings for static values
        request_context: Optional dict with structure {"headers": {"Name": "Value", ...}}.
            Passed through from the incoming HTTP request.
        source_name: Name of the toolset/component for logging purposes.

    Returns:
        Dict of rendered header name-value pairs. Headers that fail to render
        are skipped with a warning.
    """
    rendered = {}
    for header_name, header_template in extra_headers.items():
        try:
            rendered[header_name] = _render_single_template(
                header_template, request_context, allow_read_file=True
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"'{source_name}': Failed to render header template "
                f"'{header_name}': {e}"
            )
    return rendered


def _read_file(path: str) -> str:
    """Return a file's contents without surrounding whitespace.

    Token files usually end with a newline, which is not valid inside a header
    value. A missing or unreadable file raises, so the header is skipped with a
    warning rather than sent empty.
    """
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _render_single_template(
    template_str: str,
    request_context: Optional[Dict[str, Any]] = None,
    allow_read_file: bool = False,
) -> str:
    """Render a single Jinja2 template string.

    Supports:
    - {{ request_context.headers['Header-Name'] }} - case-insensitive header lookup
    - {{ env.ENV_VAR }} - environment variables
    - {{ read_file('/path') }} - only with ``allow_read_file`` (header templates):
      a file's contents, read on every render, so a credential the platform
      rotates in place (such as a projected Kubernetes service account token)
      is current on each request
    - Plain strings (no template syntax) - returned as-is

    Raises on failure so the caller can decide whether to skip or propagate.
    """
    context: Dict[str, Any] = {
        "env": os.environ,
    }
    if allow_read_file:
        context["read_file"] = _read_file

    if request_context:
        request_context_copy = request_context.copy()
        if "headers" in request_context_copy:
            request_context_copy["headers"] = CaseInsensitiveDict(
                request_context_copy["headers"]
            )
        context["request_context"] = request_context_copy
    else:
        context["request_context"] = {"headers": CaseInsensitiveDict()}

    template = Template(template_str)
    return template.render(context)


def render_env_template(value: Optional[str], source_name: str = "unknown") -> Optional[str]:
    """Render a single string with {{ env.X }} substitution at load time.

    No-op when ``value`` is ``None`` or contains no template syntax. On render
    failure, logs a warning and returns the original string so misconfiguration
    surfaces as an authentication error downstream rather than a startup crash.
    """
    if value is None or "{{" not in value:
        return value
    try:
        return _render_single_template(value)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"'{source_name}': Failed to render env template: {e}")
        return value
