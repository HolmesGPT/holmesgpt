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


class HealthCheckConditionType(str, Enum):
    """HealthCheck condition types."""

    COMPLETE = "Complete"
    FAILED = "Failed"


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


class HealthCheckStatus(BaseModel):
    """HealthCheck CRD status."""

    phase: Optional[CheckPhase] = None
    startTime: Optional[str] = None
    completionTime: Optional[str] = None
    result: Optional[CheckStatus] = None
    message: Optional[str] = None
    rationale: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    modelUsed: Optional[str] = None
    conditions: List[HealthCheckCondition] = Field(default_factory=list)
    notifications: List[NotificationStatus] = Field(default_factory=list)


class HealthCheckResource(BaseModel):
    """Complete HealthCheck CRD resource."""

    apiVersion: str = "holmesgpt.dev/v1alpha1"
    kind: str = "HealthCheck"
    metadata: dict
    spec: HealthCheckSpec
    status: HealthCheckStatus = Field(default_factory=HealthCheckStatus)


class ScheduledHealthCheckSpec(BaseModel):
    """ScheduledHealthCheck CRD spec."""

    schedule: str = Field(..., description="Cron expression")
    enabled: bool = Field(default=True)
    query: str = Field(..., min_length=1, max_length=5000)
    timeout: int = Field(default=30, ge=1, le=300)
    mode: CheckMode = Field(default=CheckMode.MONITOR)
    model: Optional[str] = None
    destinations: List[DestinationConfig] = Field(default_factory=list)


class ScheduledCheckActiveRef(BaseModel):
    """Reference to an active HealthCheck."""

    name: str
    namespace: str
    uid: str
    startTime: str


class ScheduledCheckHistoryEntry(BaseModel):
    """History entry for a scheduled check execution."""

    executionTime: str
    result: CheckStatus
    duration: float
    checkName: str
    message: str


class ScheduledHealthCheckStatus(BaseModel):
    """ScheduledHealthCheck CRD status."""

    lastScheduleTime: Optional[str] = None
    lastSuccessfulTime: Optional[str] = None
    lastResult: Optional[CheckStatus] = None
    message: Optional[str] = None
    active: List[ScheduledCheckActiveRef] = Field(default_factory=list)
    history: List[ScheduledCheckHistoryEntry] = Field(default_factory=list)
    conditions: List[HealthCheckCondition] = Field(default_factory=list)


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
