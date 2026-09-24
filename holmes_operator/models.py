"""Pydantic models for operator CRD objects."""

from enum import Enum
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

ConditionTypeT = TypeVar("ConditionTypeT", bound=str)


class CheckPhase(str, Enum):
    """Health check execution phase."""

    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"


class CheckStatus(str, Enum):
    """Health check result."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class CheckMode(str, Enum):
    """Health check mode."""

    ALERT = "alert"
    MONITOR = "monitor"


class NotificationStatusType(str, Enum):
    """Notification delivery status."""

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class ConditionStatus(str, Enum):
    """Kubernetes condition status."""

    TRUE = "True"
    FALSE = "False"
    UNKNOWN = "Unknown"


class ScheduledHealthCheckConditionType(str, Enum):
    """ScheduledHealthCheck condition types."""

    SCHEDULE_REGISTERED = "ScheduleRegistered"
    EXECUTION_FAILED = "ExecutionFailed"


class TriggeredHealthCheckConditionType(str, Enum):
    """TriggeredHealthCheck condition types."""

    READY = "Ready"
    TRIGGER_FAILED = "TriggerFailed"


class DestinationConfig(BaseModel):
    """Destination configuration for alerts."""

    type: str
    config: dict = Field(default_factory=dict)


class HealthCheckSpec(BaseModel):
    """HealthCheck CRD spec."""

    query: str = Field(..., min_length=1, max_length=5000)
    timeout: int = Field(default=30, ge=1, le=300)
    mode: CheckMode = Field(default=CheckMode.MONITOR)
    model: Optional[str] = None
    destinations: List[DestinationConfig] = Field(default_factory=list)


class NotificationStatus(BaseModel):
    """Notification delivery status."""

    type: str
    channel: Optional[str] = None
    status: NotificationStatusType
    error: Optional[str] = None


class HealthCheckCondition(BaseModel, Generic[ConditionTypeT]):
    """Kubernetes condition."""

    type: ConditionTypeT
    status: ConditionStatus
    lastTransitionTime: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None


class ScheduledHealthCheckSpec(BaseModel):
    """ScheduledHealthCheck CRD spec."""

    schedule: str = Field(..., description="Cron expression")
    enabled: bool = Field(default=True)
    query: str = Field(..., min_length=1, max_length=5000)
    timeout: int = Field(default=30, ge=1, le=300)
    mode: CheckMode = Field(default=CheckMode.MONITOR)
    model: Optional[str] = None
    destinations: List[DestinationConfig] = Field(default_factory=list)


class TriggerSelector(BaseModel):
    """Label selector for matching resources that fire a trigger."""

    matchLabels: dict = Field(default_factory=dict)


class DeploymentRolloutTrigger(BaseModel):
    """Fire when a Deployment matching the selector rolls out a new pod template."""

    selector: TriggerSelector = Field(default_factory=TriggerSelector)


class TriggeredHealthCheckSpec(BaseModel):
    """TriggeredHealthCheck CRD spec.

    Self-contained, mirroring ScheduledHealthCheck: it embeds the check definition
    inline and spawns a HealthCheck child when the trigger fires (rather than
    referencing a separate HealthCheck/template).
    """

    enabled: bool = Field(default=True)
    deploymentRollout: DeploymentRolloutTrigger
    # How long to wait after a new version is rolled out before running the check.
    # Gives the rollout time to finish and gives crashes/errors time to show up.
    # Default 5 minutes; 0 checks immediately; up to 7 days (e.g. 86400 = a day later).
    # The wait is saved on the resource, so it still completes if the operator restarts.
    delaySeconds: int = Field(default=300, ge=0, le=604800)
    # Suppress re-firing for the same Deployment within this many seconds. 0 disables.
    cooldownSeconds: int = Field(default=0, ge=0)
    # Inline HealthCheck definition (same fields as HealthCheckSpec)
    query: str = Field(..., min_length=1, max_length=5000)
    timeout: int = Field(default=120, ge=1, le=300)
    mode: CheckMode = Field(default=CheckMode.MONITOR)
    model: Optional[str] = None
    destinations: List[DestinationConfig] = Field(default_factory=list)


class CheckResponse(BaseModel):
    status: CheckStatus
    message: str
    duration: float
    rationale: Optional[str] = None
    error: Optional[str] = None
    model_used: Optional[str] = None  # The actual model that was used
    notifications: Optional[list[NotificationStatus]] = (
        None  # Notification delivery status
    )
