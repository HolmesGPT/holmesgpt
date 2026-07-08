from typing import List

from holmes.plugins.toolsets.investigator.model import Hypothesis, HypothesisStatus


def format_hypotheses(hypotheses: List[Hypothesis]) -> str:
    """
    Format root-cause hypotheses for a tool response.
    Returns empty string if no hypotheses exist.
    """
    if not hypotheses:
        return ""

    # Show the ones still in play (proposed/investigating) first, then the
    # resolved ones (supported/refuted) so the open questions stay visible.
    status_order = {
        HypothesisStatus.INVESTIGATING: 0,
        HypothesisStatus.PROPOSED: 1,
        HypothesisStatus.SUPPORTED: 2,
        HypothesisStatus.REFUTED: 3,
    }

    sorted_hypotheses = sorted(
        hypotheses,
        key=lambda h: (status_order.get(h.status, 4),),
    )

    proposed = sum(1 for h in hypotheses if h.status == HypothesisStatus.PROPOSED)
    investigating = sum(
        1 for h in hypotheses if h.status == HypothesisStatus.INVESTIGATING
    )
    supported = sum(1 for h in hypotheses if h.status == HypothesisStatus.SUPPORTED)
    refuted = sum(1 for h in hypotheses if h.status == HypothesisStatus.REFUTED)

    status_indicator = {
        HypothesisStatus.PROPOSED: "[?]",
        HypothesisStatus.INVESTIGATING: "[~]",
        HypothesisStatus.SUPPORTED: "[✓]",
        HypothesisStatus.REFUTED: "[✗]",
    }

    lines = ["# CURRENT ROOT-CAUSE HYPOTHESES", ""]
    lines.append(
        f"**Hypothesis Status**: {supported} supported, {refuted} refuted, "
        f"{investigating} investigating, {proposed} proposed"
    )
    lines.append("")

    for h in sorted_hypotheses:
        indicator = status_indicator.get(h.status, "[?]")
        line = f"{indicator} [{h.id}] ({h.status.value}) {h.statement}"
        if h.evidence:
            line += f" — evidence: {h.evidence}"
        lines.append(line)

    lines.append("")
    lines.append(
        "**Instructions**: Use HypothesisWrite to keep this list current. Only mark a "
        "hypothesis 'supported' once evidence confirms it is the cause of THIS "
        "problem, and 'refuted' once evidence rules it out. Do not conclude the "
        "investigation while a more likely hypothesis remains un-investigated."
    )

    return "\n".join(lines)
