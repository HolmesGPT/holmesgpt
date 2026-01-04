# This is patched by github actions during release
__version__ = "0.0.0"

# Re-export version functions from version module for backward compatibility
from .version import get_version as get_version
from .version import is_official_release as is_official_release
