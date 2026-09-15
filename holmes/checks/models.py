"""Data models for Holmes health checks."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CheckMode(str, Enum):
    """Mode for running checks."""

    ALERT = "alert"
    MONITOR = "monitor"


class CheckStatus(str, Enum):
    """Status of a check execution."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class DestinationConfig(BaseModel):
    """Configuration for alert destinations."""

    model_config = ConfigDict(populate_by_name=True)

    webhook_url: Optional[str] = None
    channel: Optional[str] = None
    integration_key: Optional[str] = None
    # Mattermost-specific fields (the bot token travels via config.mattermost_token
    # or the MATTERMOST_TOKEN env var — not inside the YAML destinations block).
    # Aliases match the documented per-destination YAML keys (url, channel_id, verify_ssl).
    mattermost_url: Optional[str] = Field(default=None, alias="url")
    mattermost_channel_id: Optional[str] = Field(default=None, alias="channel_id")
    mattermost_verify_ssl: Optional[bool] = Field(default=None, alias="verify_ssl")


class Check(BaseModel):
    """Individual check configuration."""

    name: str
    query: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    mode: CheckMode = CheckMode.MONITOR
    destinations: List[str] = Field(default_factory=list)
    timeout: int = 30
    schedule: Optional[str] = None  # cron format for future implementation


class ChecksConfig(BaseModel):
    """Configuration for health checks."""

    version: int = 1
    defaults: Dict[str, Any] = Field(default_factory=dict)
    destinations: Dict[str, DestinationConfig] = Field(default_factory=dict)
    checks: List[Check] = Field(default_factory=list)


class CheckResponse(BaseModel):
    """Structured response from LLM for health checks."""

    passed: bool = Field(
        description="Whether the check passed (true) or failed (false). IMPORTANT: If you cannot evaluate the check due to missing resources, unavailable metrics, or any error that prevents verification, you MUST return false (failed)."
    )
    rationale: str = Field(
        description="Brief explanation of why the check passed or failed. If unable to evaluate, explain what prevented the check from being performed."
    )


@dataclass
class CheckResult:
    """Result of a single check execution."""

    check_name: str
    status: CheckStatus
    message: str
    query: str = ""
    duration: float = 0.0
    error: Optional[str] = None
    rationale: Optional[str] = None
