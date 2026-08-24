"""Turn retrieved incidents into auditable missing-check suggestions.

A suggestion is only useful if a responder can decide, in a few seconds,
whether to act on it. So every suggestion carries three things beyond the check
itself:

- **provenance** - which resolved incident it came from, and when
- **rationale** - the human-written reason that check mattered there
- **support** - how many of the matched incidents ran it, out of how many

The wording is advisory throughout. One historical path is evidence, not a plan,
and presenting it as a required checklist would push every investigation towards
whatever was investigated first. `max_suggestions` exists for the same reason:
an unbounded list is ignored, so the report is capped and ranked by how much a
skipped check cost in the past.
"""

from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from holmes.core.investigation_path.retrieval import RetrievalResult
from holmes.core.investigation_path.schema import (
    SUBJECT_TOKEN,
    EntityRef,
    QueryIntent,
    ReferenceStep,
    SignatureLevel,
)


class SuggestionPolicy(BaseModel):
    min_support: int = Field(
        default=1, description="Matched incidents that must have run a check before it is suggested."
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
    confidence: float
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
            lines.append(
                f"  Seen in {suggestion.support}/{suggestion.out_of} similar resolved "
                f"incidents: {sources}"
            )
        return "\n".join(lines)


def validate_path(
    observed_signatures: Sequence[str],
    retrieval: RetrievalResult,
    policy: Optional[SuggestionPolicy] = None,
    subject: Optional[str] = None,
) -> ValidationReport:
    """Compare an investigation's checks against the reference paths of its matches."""
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
        # De-duplicate within one incident so a repeated reference step cannot
        # inflate its own support count.
        seen_here = set()
        for step in incident.reference_path:
            signature = step.signature(policy.signature_level)
            if signature in performed or signature in seen_here:
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
        best = max(steps, key=lambda step: step.weight)
        confidence = retrieval.confidence * (support / out_of)
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
                confidence=confidence,
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
