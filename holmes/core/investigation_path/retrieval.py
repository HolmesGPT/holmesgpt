"""Find resolved incidents similar to the one being investigated, or abstain.

Two ideas drive this module.

**Similar symptoms and same root cause are different questions.** Retrieval
ranks by symptom overlap, because symptoms are all that is known while an
incident is still open. But the checks worth suggesting come from the *cause*,
not the presentation. So after ranking, the candidates are collapsed to the
single root cause most of them share, and everything that disagrees is dropped.
If the candidates do not agree, there is no defensible cause to borrow checks
from and the retrieval abstains.

**Abstention is a first-class outcome, not an error.** A wrong suggestion costs
a responder attention during an incident, so answering is only worth it when
similarity, support and cause agreement all hold. Every abstention carries a
reason so the offline eval can report *why* coverage was lost.

The confidence score here is a deliberately simple product of three signals. It
is not assumed to be calibrated - `metrics.py` measures its calibration error,
which is the whole point of doing this offline first.
"""

from enum import Enum
from typing import List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from holmes.core.investigation_path.schema import IncidentRecord, SignatureLevel


class AbstainReason(str, Enum):
    NO_CANDIDATES = "no_candidates"
    LOW_SIMILARITY = "low_similarity"
    INSUFFICIENT_SUPPORT = "insufficient_support"
    ROOT_CAUSE_DISAGREEMENT = "root_cause_disagreement"


class RetrievalPolicy(BaseModel):
    """Every knob that decides whether to answer, and how confidently.

    The bounds are load-bearing, not decoration. This is the surface a
    contributor sweeps when exploring the recall/precision tradeoff, and an
    out-of-range value does not announce itself: a similarity floor above 1.0
    abstains on everything and a candidate cap of 0 finds nothing, both of
    which report as a policy result rather than as the typo they are.
    `full_support_matches=0` is the one that fails loudly, dividing by zero
    when confidence is computed.
    """

    min_symptom_similarity: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Symptom overlap below this is treated as no match at all.",
    )
    max_candidates: int = Field(
        default=5,
        gt=0,
        description="How many ranked candidates to consider before filtering by cause.",
    )
    min_matches: int = Field(
        default=2,
        ge=1,
        description="Answering on a single past incident overfits to it, so require at least this many.",
    )
    min_root_cause_agreement: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Share of candidates that must agree on the root cause before answering.",
    )
    full_support_matches: int = Field(
        default=3,
        gt=0,
        description="Match count at which the support term of confidence reaches 1.0.",
    )
    signature_level: SignatureLevel = SignatureLevel.FINE


class ScoredIncident(BaseModel):
    incident: IncidentRecord
    symptom_similarity: float


class RetrievalResult(BaseModel):
    """What retrieval found, including the candidates it then rejected."""

    candidates: List[ScoredIncident] = Field(
        default_factory=list, description="Everything above the similarity floor, ranked."
    )
    matches: List[ScoredIncident] = Field(
        default_factory=list, description="Candidates sharing the modal root cause. Empty when abstaining."
    )
    modal_root_cause: Optional[str] = None
    symptom_confidence: float = 0.0
    root_cause_agreement: float = 0.0
    confidence: float = Field(
        default=0.0, description="Product of symptom confidence, cause agreement and support."
    )
    abstained: bool = True
    abstain_reason: Optional[AbstainReason] = None

    def explain_abstention(self) -> str:
        reasons = {
            AbstainReason.NO_CANDIDATES: "no past incident resembled this one",
            AbstainReason.LOW_SIMILARITY: "the closest past incidents were not similar enough",
            AbstainReason.INSUFFICIENT_SUPPORT: "too few past incidents shared this root cause",
            AbstainReason.ROOT_CAUSE_DISAGREEMENT: (
                "similar past incidents had different root causes"
            ),
        }
        if self.abstain_reason is None:
            return ""
        return reasons[self.abstain_reason]


def symptom_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    """Jaccard overlap of two curated symptom keyword sets, in [0, 1]."""
    left_set, right_set = {s.lower() for s in left}, {s.lower() for s in right}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _modal_root_cause(candidates: Sequence[ScoredIncident]) -> Tuple[Optional[str], float]:
    """The most common root-cause label among candidates, and its share.

    Ties are broken by summed similarity so the winner is the cause the closest
    incidents point at, not just the alphabetically first one.
    """
    if not candidates:
        return None, 0.0
    counts: dict = {}
    weights: dict = {}
    for candidate in candidates:
        label = candidate.incident.root_cause.label
        counts[label] = counts.get(label, 0) + 1
        weights[label] = weights.get(label, 0.0) + candidate.symptom_similarity
    label = max(counts, key=lambda key: (counts[key], weights[key]))
    return label, counts[label] / len(candidates)


def retrieve(
    symptoms: Sequence[str],
    corpus: Sequence[IncidentRecord],
    policy: Optional[RetrievalPolicy] = None,
) -> RetrievalResult:
    """Rank the corpus by symptom overlap and decide whether it is safe to answer."""
    policy = policy or RetrievalPolicy()

    scored = [
        ScoredIncident(incident=incident, symptom_similarity=symptom_similarity(symptoms, incident.symptoms))
        for incident in corpus
    ]
    above_floor = [s for s in scored if s.symptom_similarity >= policy.min_symptom_similarity]
    # Ties break on incident id ascending so that two equally similar incidents
    # always rank in the same order, whatever order the corpus loaded in.
    above_floor.sort(key=lambda s: (-s.symptom_similarity, s.incident.incident_id))
    candidates = above_floor[: policy.max_candidates]

    if not candidates:
        any_overlap = any(s.symptom_similarity > 0 for s in scored)
        return RetrievalResult(
            abstained=True,
            abstain_reason=AbstainReason.LOW_SIMILARITY if any_overlap else AbstainReason.NO_CANDIDATES,
        )

    modal_label, agreement = _modal_root_cause(candidates)
    base = RetrievalResult(
        candidates=candidates,
        modal_root_cause=modal_label,
        root_cause_agreement=agreement,
    )

    if agreement < policy.min_root_cause_agreement:
        return base.model_copy(
            update={"abstained": True, "abstain_reason": AbstainReason.ROOT_CAUSE_DISAGREEMENT}
        )

    matches = [c for c in candidates if c.incident.root_cause.label == modal_label]
    if len(matches) < policy.min_matches:
        return base.model_copy(
            update={"abstained": True, "abstain_reason": AbstainReason.INSUFFICIENT_SUPPORT}
        )

    symptom_confidence = sum(m.symptom_similarity for m in matches) / len(matches)
    support = min(1.0, len(matches) / policy.full_support_matches)

    return base.model_copy(
        update={
            "matches": matches,
            "symptom_confidence": symptom_confidence,
            "confidence": symptom_confidence * agreement * support,
            "abstained": False,
            "abstain_reason": None,
        }
    )
