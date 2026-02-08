"""Configuration for Holmes Operator."""

import os
from dataclasses import dataclass


@dataclass
class OperatorConfig:
    """Configuration for Holmes Operator loaded from environment variables."""

    # Holmes API connection
    holmes_api_url: str
    holmes_api_timeout: int

    # Operator behavior
    log_level: str
    enable_metrics: bool
    metrics_port: int

    # History and cleanup
    max_history_items: int
    cleanup_completed_checks: bool
    completed_check_ttl_hours: int

    # Scheduler
    scheduler_timezone: str

    @classmethod
    def load(cls) -> "OperatorConfig":
        """Load configuration from environment variables."""
        return cls(
            holmes_api_url=os.getenv("HOLMES_API_URL", "http://holmes-api:80"),
            holmes_api_timeout=int(os.getenv("HOLMES_API_TIMEOUT", "300")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            enable_metrics=os.getenv("ENABLE_METRICS", "true").lower() == "true",
            metrics_port=int(os.getenv("METRICS_PORT", "8080")),
            max_history_items=int(os.getenv("MAX_HISTORY_ITEMS", "10")),
            cleanup_completed_checks=os.getenv(
                "CLEANUP_COMPLETED_CHECKS", "false"
            ).lower()
            == "true",
            completed_check_ttl_hours=int(os.getenv("COMPLETED_CHECK_TTL_HOURS", "24")),
            scheduler_timezone=os.getenv("SCHEDULER_TIMEZONE", "UTC"),
        )
