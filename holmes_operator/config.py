"""Configuration for Holmes Operator."""

import json
import os
from dataclasses import dataclass
from typing import Optional


def load_bool(env_var, default: Optional[bool]) -> Optional[bool]:
    env_value = os.environ.get(env_var)
    if env_value is None:
        return default

    return json.loads(env_value.lower())


HOLMES_API_URL = os.getenv("HOLMES_API_URL", "http://holmes-api:80")
HOLMES_API_TIMEOUT = int(os.getenv("HOLMES_API_TIMEOUT", "300"))
# The key the operator sends to the Holmes API. The Helm chart mounts its
# generated key as a file and sets HOLMES_API_KEY_FILE; the file is re-read on
# every request, so a rotated Secret is picked up without a restart.
# HOLMES_API_KEY (inline) takes precedence when set.
HOLMES_API_KEY_FILE = os.getenv("HOLMES_API_KEY_FILE", "")
# CA bundle for an https:// HOLMES_API_URL (the chart sets it when tls is on).
HOLMES_API_CA_FILE = os.getenv("HOLMES_API_CA_FILE", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_HISTORY_ITEMS = int(os.getenv("MAX_HISTORY_ITEMS", "10"))
CLEANUP_COMPLETED_CHECKS = load_bool("CLEANUP_COMPLETED_CHECKS", False)
COMPLETED_CHECK_TTL_HOURS = int(os.getenv("COMPLETED_CHECK_TTL_HOURS", "24"))
ENABLE_JSON_LOGS_FORMAT = load_bool("ENABLE_JSON_LOGS_FORMAT", False)


@dataclass
class OperatorConfig:
    """Configuration for Holmes Operator loaded from environment variables."""

    # Holmes API connection
    holmes_api_url: str
    holmes_api_timeout: int

    # Operator behavior
    log_level: str

    # History and cleanup
    max_history_items: int
    cleanup_completed_checks: bool
    completed_check_ttl_hours: int

    # File holding the key to send to the Holmes API ("" = none)
    holmes_api_key_file: str = ""
    # CA bundle for an https:// Holmes API URL ("" = system trust store)
    holmes_api_ca_file: str = ""

    @classmethod
    def load(cls) -> "OperatorConfig":
        """Load configuration from environment variables."""
        return cls(
            holmes_api_url=HOLMES_API_URL,
            holmes_api_timeout=HOLMES_API_TIMEOUT,
            holmes_api_key_file=HOLMES_API_KEY_FILE,
            holmes_api_ca_file=HOLMES_API_CA_FILE,
            log_level=LOG_LEVEL,
            max_history_items=MAX_HISTORY_ITEMS,
            cleanup_completed_checks=CLEANUP_COMPLETED_CHECKS,
            completed_check_ttl_hours=COMPLETED_CHECK_TTL_HOURS,
        )
