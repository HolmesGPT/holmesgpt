"""Toolset plugin package.

The actual loading/parsing logic has moved to :mod:`holmes.core.toolset_registry`.
This module re-exports the public functions for backwards compatibility (tests
and other modules import from ``holmes.plugins.toolsets`` directly).
"""

from holmes.core.toolset_registry import (  # noqa: F401
    _discover_builtin_toolsets as load_builtin_toolsets,
    _discover_python_toolsets as load_python_toolsets,
    _load_toolsets_from_file as load_toolsets_from_file,
    _is_old_toolset_config as is_old_toolset_config,
    _parse_toolset_config as load_toolsets_from_config,
)
