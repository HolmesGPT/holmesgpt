"""Canonical investigation paths and offline path-completeness validation.

Groundwork for https://github.com/HolmesGPT/holmesgpt/issues/2046. Nothing in
this package is wired into the investigation loop: it exists so the retrieval
policy can be measured offline, against a human-validated corpus, before any
product behavior changes.

The pieces are:

- `schema`      - the redacted canonical path event and the corpus record format
- `normalize`   - executed tool calls -> canonical path events (with redaction)
- `retrieval`   - find similar resolved incidents, or abstain
- `validator`   - turn retrieved incidents into missing-check suggestions
- `calibration` - make the reported confidence mean what it says
- `metrics`     - how a retrieval policy is scored offline
- `corpus`      - load the fixture corpus from disk
- `offline_eval`- run a policy over the held-out set and score it

`offline_eval` is deliberately not re-exported here: it is run as a script
(`python -m holmes.core.investigation_path.offline_eval`), and importing it from
the package `__init__` makes the module load twice under `-m`.
"""

from holmes.core.investigation_path.calibration import (
    CalibrationModel,
    fit_calibration,
    fit_platt,
)
from holmes.core.investigation_path.corpus import load_corpus
from holmes.core.investigation_path.metrics import (
    CaseOutcome,
    EvalMetrics,
    score_cases,
)
from holmes.core.investigation_path.normalize import (
    normalize_resource_kind,
    normalize_resource_name,
    path_from_tool_calls,
)
from holmes.core.investigation_path.retrieval import (
    RetrievalPolicy,
    RetrievalResult,
    ScoredIncident,
    retrieve,
)
from holmes.core.investigation_path.schema import (
    SUBJECT_TOKEN,
    EntityRef,
    IncidentRecord,
    InvestigationPath,
    Outcome,
    OutcomeStatus,
    PathEvent,
    QueryIntent,
    ReferenceStep,
    RootCause,
    SignatureLevel,
    TimeWindow,
    signature_of,
)
from holmes.core.investigation_path.validator import (
    Provenance,
    Suggestion,
    SuggestionPolicy,
    ValidationReport,
    validate_path,
)

__all__ = [
    "SUBJECT_TOKEN",
    "CalibrationModel",
    "CaseOutcome",
    "EntityRef",
    "EvalMetrics",
    "IncidentRecord",
    "InvestigationPath",
    "Outcome",
    "OutcomeStatus",
    "PathEvent",
    "Provenance",
    "QueryIntent",
    "ReferenceStep",
    "RetrievalPolicy",
    "RetrievalResult",
    "RootCause",
    "ScoredIncident",
    "SignatureLevel",
    "Suggestion",
    "SuggestionPolicy",
    "TimeWindow",
    "ValidationReport",
    "fit_calibration",
    "fit_platt",
    "load_corpus",
    "normalize_resource_kind",
    "normalize_resource_name",
    "path_from_tool_calls",
    "score_cases",
    "signature_of",
    "validate_path",
]
