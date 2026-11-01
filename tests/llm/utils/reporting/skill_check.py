"""Skill-suggestion checks that fail a run independently of the judge.

test_ask_holmes.py fails a run that emits a skill where the eval expects none,
emits none where it expects one, or misses an expected skill update. The
judge's correctness is kept as the judge returned it, so reports mark these
runs with SKILL_CHECK_MARKER instead of showing a plain wrong answer. They
still count as failures.
"""

import re
from enum import Enum
from typing import Any, Dict, List, Optional

SKILL_CHECK_MARKER = "🧩"


class SkillCheckFailure(str, Enum):
    UNEXPECTED_SUGGESTION = "unexpected_skill_suggestion"
    MISSING_SUGGESTION = "missing_skill_suggestion"
    MISSING_UPDATE = "missing_skill_update"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    SkillCheckFailure.UNEXPECTED_SUGGESTION: "suggested a skill, expected none",
    SkillCheckFailure.MISSING_SUGGESTION: "suggested no skill, expected one",
    SkillCheckFailure.MISSING_UPDATE: "did not update the loaded skill",
}

# Reports written before `fail_reason` existed only carry the assertion text
# raised by test_ask_holmes.py; these phrases must stay in sync with it.
_ASSERTION_PHRASES = {
    "expected NO skill suggestions": SkillCheckFailure.UNEXPECTED_SUGGESTION,
    "expected at least one skill suggestion": SkillCheckFailure.MISSING_SUGGESTION,
    "expected suggestion(s) correcting the loaded skill(s)": (
        SkillCheckFailure.MISSING_UPDATE
    ),
}
# Only the exception's own message counts: a long traceback also prints the
# test source, which contains every phrase above.
_EXCEPTION_LINE = re.compile(r"^E +(.*)$", re.MULTILINE)


def failure_message(call: Optional[Dict[str, Any]]) -> str:
    """The raised exception's message from a pytest-json-report `call` entry."""
    if not call:
        return ""
    crash = call.get("crash") or {}
    if crash.get("message"):
        return str(crash["message"])
    return "\n".join(_EXCEPTION_LINE.findall(str(call.get("longrepr") or "")))


def classify_skill_check_failure(
    props: Dict[str, Any], failure_text: str = ""
) -> Optional[SkillCheckFailure]:
    """Skill check a failed run tripped, from `fail_reason` or the assertion text."""
    reason = props.get("fail_reason")
    if reason:
        try:
            return SkillCheckFailure(reason)
        except ValueError:
            return None
    for phrase, failure in _ASSERTION_PHRASES.items():
        if phrase in failure_text:
            return failure
    return None


def skill_titles(suggestions: Any) -> List[str]:
    if not isinstance(suggestions, list):
        return []
    return [
        str(s["title"]) for s in suggestions if isinstance(s, dict) and s.get("title")
    ]


def suggested_skill_titles(props: Dict[str, Any]) -> List[str]:
    titles = props.get("suggested_skill_titles")
    if isinstance(titles, list):
        return [str(t) for t in titles]
    return skill_titles(props.get("suggested_memories"))


def judge_verdict(props: Dict[str, Any]) -> Optional[bool]:
    """The judge's correctness for a skill-check failure, None when not recorded.

    Before `fail_reason` existed the skill check overwrote the judge's score
    with 0, so a 0 in those reports says nothing about the answer.
    """
    if not props.get("fail_reason"):
        return None
    try:
        return int(float(props.get("actual_correctness_score", 0))) == 1
    except (TypeError, ValueError):
        return None
