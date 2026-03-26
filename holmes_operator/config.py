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
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_HISTORY_ITEMS = int(os.getenv("MAX_HISTORY_ITEMS", "10"))
CLEANUP_COMPLETED_CHECKS = load_bool("CLEANUP_COMPLETED_CHECKS", False)
COMPLETED_CHECK_TTL_HOURS = int(os.getenv("COMPLETED_CHECK_TTL_HOURS", "24"))


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

    @classmethod
    def load(cls) -> "OperatorConfig":
        """Load configuration from environment variables."""
        return cls(
            holmes_api_url=HOLMES_API_URL,
            holmes_api_timeout=HOLMES_API_TIMEOUT,
            log_level=LOG_LEVEL,
            max_history_items=MAX_HISTORY_ITEMS,
            cleanup_completed_checks=CLEANUP_COMPLETED_CHECKS,
            completed_check_ttl_hours=COMPLETED_CHECK_TTL_HOURS,
        )
