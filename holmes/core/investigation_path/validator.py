"""Turn retrieved incidents into auditable missing-check suggestions.

A suggestion is only useful if a responder can decide, in a few seconds,
whether to act on it. So every suggestion carries four things beyond the check
itself:

- **provenance** - which resolved incident it came from, and when
- **rationale** - the human-written reason that check mattered there
- **support** - how many of the matched incidents ran it, out of how many
- **confidence** - stated as a probability *only* when a fitted calibration
  model produced it. Uncalibrated, the score is a product of terms below 1: it
  ranks correctly but reads far below the real hit rate, and showing it as a
  percentage would train responders to ignore the whole block.

The wording is advisory throughout. One historical path is evidence, not a plan,
and presenting it as a required checklist would push every investigation towards
whatever was investigated first. `max_suggestions` exists for the same reason:
an unbounded list is ignored, so the report is capped and ranked by how much a
skipped check cost in the past.
"""

import re
from typing import Dict, List, Optional, Sequence, Set

from pydantic import BaseModel, Field

from holmes.core.investigation_path.calibration_model import CalibrationModel
from holmes.core.investigation_path.retrieval import RetrievalResult
from holmes.core.investigation_path.schema import (
    SUBJECT_TOKEN,
    EntityRef,
    IncidentRecord,
    QueryIntent,
    ReferenceStep,
    SignatureLevel,
)


# Kinds whose names usually mean the same thing in every incident. A metric
# called `container_memory_working_set_bytes` exists in any cluster, so
# suggesting it transfers. A pod called `redis` does not: in an incident about a
# message broker, that suggestion sends the responder to a service that is not
# there.
GENERIC_ENTITY_KINDS = frozenset({"metric", "trace"})

_NAME_TOKEN_RE = re.compile(r"[^a-z0-9]+")


class SuggestionPolicy(BaseModel):
    min_support: int = Field(
        default=1, description="Matched incidents that must have run a check before it is suggested."
    )
    min_support_ratio: float = Field(
        default=0.6,
        description=(
            "Share of matched incidents that must have run a check. A check only one "
            "incident out of three ran is that incident's particular circumstance, not "
            "a property of the root cause, and suggesting it is how false positives get in."
        ),
    )
    min_confidence: float = Field(
        default=0.15, description="Suggestions below this confidence are dropped rather than shown."
    )
    max_suggestions: int = Field(
        default=5, description="Cap on suggestions per report, to bound the reading cost."
    )
    signature_level: SignatureLevel = SignatureLevel.FINE


class Provenance(BaseModel):
    """Where one suggestion came from, so the user can go and check."""

    incident_id: str
    title: str
    occurred_at: str
    root_cause_label: str


class Suggestion(BaseModel):
    signature: str
    intent: QueryIntent
    entity: EntityRef
    rationale: str
    weight: float = Field(description="Importance of this check in the reference paths that had it.")
    support: int
    out_of: int
    raw_confidence: float = Field(
        description="Uncalibrated evidence score. Useful for ranking, not a probability."
    )
    confidence: float = Field(
        description="Calibrated probability that this check is genuinely missing, "
        "when a calibration model is supplied. Equals raw_confidence otherwise."
    )
    calibrated: bool = Field(
        default=False,
        description=(
            "Whether `confidence` came from a fitted calibration model. False means "
            "it is a raw evidence score that orders suggestions correctly but is not "
            "a probability, and must not be rendered to a user as a percentage."
        ),
    )
    provenance: List[Provenance] = Field(default_factory=list)

    def describe(self, subject: Optional[str] = None) -> str:
        rendered = self.entity.describe()
        if subject:
            rendered = rendered.replace(SUBJECT_TOKEN, subject)
        return f"{self.intent.value} {rendered}"


class ValidationReport(BaseModel):
    """The outcome of validating one investigation path."""

    abstained: bool
    abstain_reason: Optional[str] = None
    modal_root_cause: Optional[str] = None
    confidence: float = 0.0
    subject: Optional[str] = Field(
        default=None, description="Workload under investigation, used to render SUBJECT_TOKEN."
    )
    suggestions: List[Suggestion] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.suggestions

    def to_markdown(self) -> str:
        """Render the advisory block a user would see. Not wired into any output yet."""
        if self.abstained:
            return ""
        if self.is_empty:
            return ""

        lines = [
            "## Investigation path check",
            "",
            "Resolved incidents with the same root cause "
            f"(`{self.modal_root_cause}`) also ran the checks below. "
            "These are suggestions from past evidence, not required steps - "
            "skip any that do not apply here.",
            "",
        ]
        for suggestion in self.suggestions:
            lines.append(f"- **{suggestion.describe(self.subject)}** — {suggestion.rationale}")
            sources = ", ".join(
                f"{p.incident_id} ({p.occurred_at})" for p in suggestion.provenance
            )
            evidence = (
                f"  Seen in {suggestion.support}/{suggestion.out_of} similar resolved "
                f"incidents: {sources}"
            )
            # Only a calibrated score is stated as a probability. The raw score is
            # a product of terms below 1, so printing it as a percentage would
            # understate the real hit rate and teach responders to ignore it.
            if suggestion.calibrated:
                evidence += f". Confidence {suggestion.confidence:.0%}"
            lines.append(evidence)
        return "\n".join(lines)


def _foreign_entities(
    incident: IncidentRecord, known_entities: Optional[Set[str]]
) -> Set[str]:
    """Objects a past incident named that the current investigation has never seen."""
    if known_entities is None:
        return set()
    return {
        step.entity.name
        for step in incident.reference_path
        if step.entity.name
        and step.entity.name != SUBJECT_TOKEN
        and step.entity.kind not in GENERIC_ENTITY_KINDS
        and step.entity.name not in known_entities
    }


def _names_a_foreign_entity(name: str, foreign: Set[str]) -> bool:
    """True for a generic-kind name built out of a foreign object's name.

    `redis_connected_clients` is nominally a metric, and metric names normally
    transfer between incidents - but that one only exists where Redis does.
    Matching whole tokens rather than substrings so that a metric like
    `node_memory_MemAvailable_bytes` is not rejected because some incident
    happened to name a pod `mem`.
    """
    tokens = set(_NAME_TOKEN_RE.split(name.lower())) - {""}
    return any(entity.lower() in tokens for entity in foreign)


def _transfers_to_this_incident(
    entity: EntityRef, known_entities: Optional[Set[str]], foreign: Set[str]
) -> bool:
    """Whether a reference check makes sense against the incident being validated.

    Returns True when no `known_entities` set was supplied, so callers that
    cannot determine it are not silently filtered.
    """
    if known_entities is None:
        return True
    if entity.name is None or entity.name == SUBJECT_TOKEN:
        return True
    if entity.kind in GENERIC_ENTITY_KINDS:
        return not _names_a_foreign_entity(entity.name, foreign)
    return entity.name in known_entities


def validate_path(
    observed_signatures: Sequence[str],
    retrieval: RetrievalResult,
    policy: Optional[SuggestionPolicy] = None,
    subject: Optional[str] = None,
    calibration: Optional[CalibrationModel] = None,
    known_entities: Optional[Set[str]] = None,
) -> ValidationReport:
    """Compare an investigation's checks against the reference paths of its matches.

    Without a `calibration` model the reported confidence is the raw evidence
    score, which orders suggestions correctly but is not a probability and must
    not be shown to a user as one.

    `known_entities` is the set of object names the current investigation has
    actually seen. Pass it to suppress suggestions that name something from a
    past incident which does not exist in this one - two incidents can share
    symptoms and a root cause while depending on entirely different services.
    """
    policy = policy or SuggestionPolicy()

    if retrieval.abstained:
        return ValidationReport(
            abstained=True,
            abstain_reason=retrieval.explain_abstention(),
            modal_root_cause=retrieval.modal_root_cause,
            confidence=retrieval.confidence,
            subject=subject,
        )

    performed = set(observed_signatures)
    out_of = len(retrieval.matches)

    steps_by_signature: Dict[str, List[ReferenceStep]] = {}
    provenance_by_signature: Dict[str, List[Provenance]] = {}
    for match in retrieval.matches:
        incident = match.incident
        foreign = _foreign_entities(incident, known_entities)
        # De-duplicate within one incident so a repeated reference step cannot
        # inflate its own support count.
        seen_here = set()
        for step in incident.reference_path:
            signature = step.signature(policy.signature_level)
            if signature in performed or signature in seen_here:
                continue
            if not _transfers_to_this_incident(step.entity, known_entities, foreign):
                continue
            seen_here.add(signature)
            steps_by_signature.setdefault(signature, []).append(step)
            provenance_by_signature.setdefault(signature, []).append(
                Provenance(
                    incident_id=incident.incident_id,
                    title=incident.title,
                    occurred_at=incident.occurred_at,
                    root_cause_label=incident.root_cause.label,
                )
            )

    suggestions: List[Suggestion] = []
    for signature, steps in steps_by_signature.items():
        support = len(steps)
        if support < policy.min_support:
            continue
        if support / out_of < policy.min_support_ratio:
            continue
        best = max(steps, key=lambda step: step.weight)
        raw_confidence = retrieval.confidence * (support / out_of)
        is_calibrated = calibration is not None and calibration.fitted
        confidence = calibration.apply(raw_confidence) if calibration else raw_confidence
        if confidence < policy.min_confidence:
            continue
        suggestions.append(
            Suggestion(
                signature=signature,
                intent=best.intent,
                entity=best.entity,
                rationale=best.rationale,
                weight=best.weight,
                support=support,
                out_of=out_of,
                raw_confidence=raw_confidence,
                confidence=confidence,
                calibrated=is_calibrated,
                provenance=provenance_by_signature[signature],
            )
        )

    suggestions.sort(key=lambda s: (-(s.weight * s.support), s.signature))
    return ValidationReport(
        abstained=False,
        modal_root_cause=retrieval.modal_root_cause,
        confidence=retrieval.confidence,
        subject=subject,
        suggestions=suggestions[: policy.max_suggestions],
    )
