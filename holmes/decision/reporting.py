"""Render report summaries only from validated structured output."""

import re

from holmes.decision.models import DecisionState, Diagnosis, DiagnosisDraft, DiagnosisOutcome


def render_diagnosis(draft: DiagnosisDraft, state: DecisionState, valid_ids: set[str]) -> Diagnosis:
    draft.validate_references(valid_ids)
    chinese = bool(re.search(r"[\u4e00-\u9fff]", state.question))
    if draft.causes:
        summary = (f"发现 {len(draft.causes)} 个候选原因，仍需验证，尚未确认根因。" if chinese
                   else f"Found {len(draft.causes)} candidate cause(s); verification is required and no root cause is confirmed.")
        outcome: DiagnosisOutcome = "candidate_causes"
    else:
        summary = ("当前证据不足，未形成诊断结论。" if chinese
                   else "Evidence is insufficient; no diagnosis has been established.")
        outcome = "insufficient_evidence"
    return Diagnosis(
        summary=summary, causes=draft.causes, verification_steps=draft.verification_steps,
        limitations=draft.limitations, schema_version=2, outcome=outcome,
    )
