"""The redacted canonical path event, and the corpus record built on top of it.

An investigation is reduced to an ordered list of `PathEvent`s. Each event says
*what was checked*, *why*, *over what time range* and *how it turned out* - and
deliberately nothing else. Two rules govern every field here:

1. **Nothing unbounded or secret is stored.** No raw tool output, no raw error
   strings, no credentials, no free-form provider parameters. Error text is
   collapsed to an `error_class`; evidence is an opaque `evidence_ref` that
   points at output held elsewhere rather than a copy of it.
2. **Nothing unstable is stored.** Values that change between two runs of the
   same check - pod hashes, exact timestamps, request ids - are normalized or
   bucketed away, so that the same check in two incidents compares equal.

`signature_of()` is what comparison actually runs on. It exists at two
granularities because they answer different questions: COARSE asks "did anyone
look at the service topology at all", FINE asks "did anyone look at
`service/redis`".
"""

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# Stands in for "the workload this incident is about" inside a stored path.
# Without it, a reference path from a payment-service incident could never match
# an orders-service one, because every signature would carry a workload name
# that appears in exactly one incident. Corpus paths are authored with this
# token; live paths get it substituted in by passing `subject=` to the signature
# helpers.
SUBJECT_TOKEN = "<subject>"

# Lookback windows are bucketed to these boundaries (in seconds) so that
# "--since 5m" and "--since 7m" do not look like two different checks.
LOOKBACK_BUCKETS_SECONDS = (
    300,  # 5m
    900,  # 15m
    3600,  # 1h
    21600,  # 6h
    86400,  # 24h
    604800,  # 7d
)


class QueryIntent(str, Enum):
    """Why a check was run, independent of which tool ran it.

    Intent is what makes paths comparable across toolsets: `kubectl logs`,
    `fetch_pod_logs` and a Loki query are all `LOGS`, so an investigation that
    used one is not told it skipped the others.
    """

    DESCRIBE = "describe"  # inspect one named object's spec/status
    LIST = "list"  # enumerate objects of a kind
    LOGS = "logs"
    EVENTS = "events"
    METRICS = "metrics"
    TRACES = "traces"
    TOPOLOGY = "topology"  # services, endpoints, reachability, ownership
    RESOURCE_USAGE = "resource_usage"  # utilization, limits, capacity
    CONFIG_HISTORY = "config_history"  # deploys, rollouts, config changes
    OTHER = "other"


class OutcomeStatus(str, Enum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    ERROR = "error"


class ErrorClass(str, Enum):
    """Normalized failure reason. Raw error text is never stored."""

    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    TIMEOUT = "timeout"
    INVALID_QUERY = "invalid_query"
    UNAVAILABLE = "unavailable"
    OTHER = "other"


class SignatureLevel(str, Enum):
    """How precisely two checks must match to count as the same check."""

    COARSE = "coarse"  # intent + entity kind
    FINE = "fine"  # intent + entity kind + entity name


class EntityRef(BaseModel):
    """What a check looked at, normalized so instances of one workload match.

    `name` has generated suffixes stripped by `normalize.normalize_resource_name`,
    so `payment-service-7d9f8b6c5-x2k9p` is stored as `payment-service`.
    """

    kind: Optional[str] = Field(
        default=None,
        description="Normalized singular kind: 'pod', 'service', 'metric', 'trace'.",
    )
    name: Optional[str] = Field(
        default=None, description="Normalized object or metric name, without instance suffixes."
    )
    namespace: Optional[str] = Field(
        default=None,
        description="Kept for provenance display only; never part of a signature.",
    )

    def describe(self) -> str:
        if self.kind and self.name:
            return f"{self.kind}/{self.name}"
        return self.name or self.kind or "-"


class TimeWindow(BaseModel):
    """The time range a check covered, bucketed so it is stable across runs."""

    lookback_seconds: Optional[int] = Field(
        default=None,
        description="Relative lookback, snapped to LOOKBACK_BUCKETS_SECONDS.",
    )
    is_point_in_time: bool = Field(
        default=False,
        description="True for checks that read current state rather than a range.",
    )

    def describe(self) -> str:
        if self.is_point_in_time:
            return "now"
        if self.lookback_seconds is None:
            return "unspecified"
        return _humanize_seconds(self.lookback_seconds)


class Outcome(BaseModel):
    """How a check turned out, with the failure reason collapsed to a class."""

    status: OutcomeStatus
    error_class: Optional[ErrorClass] = None


class PathEvent(BaseModel):
    """One check inside an investigation, in canonical redacted form."""

    ordinal: int = Field(description="0-based position in the investigation, preserving order.")
    tool: str = Field(description="Tool name as executed. Kept for provenance, not for matching.")
    intent: QueryIntent
    entity: EntityRef = Field(default_factory=EntityRef)
    time_window: Optional[TimeWindow] = None
    outcome: Outcome
    evidence_ref: Optional[str] = Field(
        default=None,
        description="Opaque pointer to the output (e.g. a tool call id). Never the output itself.",
    )

    def describe(self) -> str:
        return f"{self.intent.value} {self.entity.describe()}"


class InvestigationPath(BaseModel):
    """An ordered, de-duplicated view of everything one investigation checked."""

    events: List[PathEvent] = Field(default_factory=list)

    def signatures(
        self,
        level: SignatureLevel = SignatureLevel.FINE,
        subject: Optional[str] = None,
    ) -> List[str]:
        """Signatures in execution order, first occurrence wins."""
        seen: set = set()
        ordered: List[str] = []
        for event in self.events:
            key = signature_of(event, level, subject)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered

    def signature_set(
        self,
        level: SignatureLevel = SignatureLevel.FINE,
        subject: Optional[str] = None,
    ) -> set:
        return set(self.signatures(level, subject))


class RootCause(BaseModel):
    """What an incident actually turned out to be.

    Held separately from symptoms on purpose: two incidents can present
    identically and still have unrelated causes, and suggesting the checks from
    the wrong cause is worse than suggesting nothing.
    """

    label: str = Field(description="Controlled vocabulary id, e.g. 'dependency_unreachable'.")
    summary: str = Field(description="One line of human-readable explanation.")


class ReferenceStep(BaseModel):
    """A check a human decided belongs in the path for this kind of incident."""

    intent: QueryIntent
    entity: EntityRef = Field(default_factory=EntityRef)
    weight: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="How much skipping this check would have hurt. Drives weighted recall.",
    )
    rationale: str = Field(
        description="Why this check matters. Shown to the user so a suggestion is auditable."
    )

    def signature(self, level: SignatureLevel = SignatureLevel.FINE) -> str:
        return _signature(self.intent, self.entity, level)


class IncidentRecord(BaseModel):
    """One human-validated resolved incident in the corpus."""

    incident_id: str
    occurred_at: str
    source_type: str
    title: str
    subject: Optional[str] = Field(
        default=None,
        description="The workload the incident was about. Paths refer to it as SUBJECT_TOKEN.",
    )
    symptoms: List[str] = Field(
        default_factory=list, description="Curated keywords describing how the incident presented."
    )
    root_cause: RootCause
    reference_path: List[ReferenceStep] = Field(
        default_factory=list,
        description="The checks a good investigation of this incident should include.",
    )
    observed_path: List[ReferenceStep] = Field(
        default_factory=list,
        description="What an investigation actually did. Only set on held-out cases, "
        "where reference_path minus observed_path is the ground-truth missing set.",
    )
    validated_by: str = Field(description="Who reviewed this record. Corpus entries are not auto-generated.")
    validated_at: str
    split: Literal["corpus", "holdout"] = "corpus"

    def reference_signatures(self, level: SignatureLevel = SignatureLevel.FINE) -> set:
        return {step.signature(level) for step in self.reference_path}

    def observed_signatures(self, level: SignatureLevel = SignatureLevel.FINE) -> set:
        return {step.signature(level) for step in self.observed_path}

    def missing_steps(self, level: SignatureLevel = SignatureLevel.FINE) -> List[ReferenceStep]:
        """Ground truth: reference checks the observed investigation did not run."""
        observed = self.observed_signatures(level)
        return [step for step in self.reference_path if step.signature(level) not in observed]


def signature_of(
    event: PathEvent,
    level: SignatureLevel = SignatureLevel.FINE,
    subject: Optional[str] = None,
) -> str:
    """The comparison key for a path event.

    Note that `tool` is intentionally excluded: the same check run through two
    different toolsets must compare equal, or an investigation gets told it
    skipped a check it actually performed.
    """
    return _signature(event.intent, event.entity, level, subject)


def _signature(
    intent: QueryIntent,
    entity: EntityRef,
    level: SignatureLevel,
    subject: Optional[str] = None,
) -> str:
    kind = entity.kind or "-"
    if level == SignatureLevel.COARSE:
        return f"{intent.value}:{kind}"
    name = entity.name or "-"
    if subject and name == normalize_subject(subject):
        name = SUBJECT_TOKEN
    return f"{intent.value}:{kind}:{name}"


def normalize_subject(subject: str) -> str:
    return subject.strip().lower()


def bucket_lookback_seconds(seconds: Optional[float]) -> Optional[int]:
    """Snap a lookback to the nearest bucket boundary at or above it."""
    if seconds is None or seconds <= 0:
        return None
    for bucket in LOOKBACK_BUCKETS_SECONDS:
        if seconds <= bucket:
            return bucket
    return LOOKBACK_BUCKETS_SECONDS[-1]


def _humanize_seconds(seconds: int) -> str:
    for amount, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds % amount == 0:
            return f"{seconds // amount}{unit}"
    return f"{seconds}s"
