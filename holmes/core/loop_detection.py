"""Detection of unproductive repetition loops in the agentic loop.

Background
----------
``prevent_overly_repeated_tool_call`` (see ``safeguards.py``) blocks a tool call
whose ``(tool_name, params)`` pair is byte-identical to one already made in the
session. That catches the simplest loop, but it misses the shapes that actually
burn a long investigation:

* the model varies a parameter trivially each turn (``limit: 100`` -> ``101``),
  so every call looks "new" to the exact-match guard;
* the model ping-pongs between two tool calls (A, B, A, B, ...);
* every tool call fails the same way and the model keeps retrying;
* the model narrates the same intent over and over ("Let me examine the API",
  "I will now search the API", "Let me look at the API...");
* a single completion degenerates into a repeated phrase until it hits the
  output token limit -- common on reasoning models decoded near-greedily.

It also misses everything after a compaction, because
``RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION`` deliberately clears the
history the exact-match guard consults. This detector works on a sliding window
of *consecutive* turns instead, so compaction does not blind it.

Design
------
Follows the "break the loop in band, not with an exception" approach used by
other harnesses (OpenHands' stuck detector, Claude Code's loop guard): when a
loop is detected we push a message into the transcript telling the model it is
repeating itself, so it gets a clean chance to change course. Only if it keeps
looping do we escalate -- first by withdrawing the tools (which forces a final
answer on the next call), and the existing ``max_steps`` cap remains as the
last resort.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence

from holmes.common.env_vars import (
    LOOP_DETECTION_ALTERNATION_CYCLES,
    LOOP_DETECTION_DEGENERATE_MIN_WORDS,
    LOOP_DETECTION_DEGENERATE_RATIO,
    LOOP_DETECTION_ENABLED,
    LOOP_DETECTION_ERROR_STREAK,
    LOOP_DETECTION_MAX_NUDGES,
    LOOP_DETECTION_NARRATION_REPEATS,
    LOOP_DETECTION_NARRATION_SIMILARITY,
    LOOP_DETECTION_REPEAT_THRESHOLD,
    LOOP_DETECTION_WINDOW,
)

# Loop kinds, used for logging/telemetry and to keep the nudge text testable.
KIND_REPEATED_TOOL_CALLS = "repeated_tool_calls"
KIND_ALTERNATING_TOOL_CALLS = "alternating_tool_calls"
KIND_REPEATED_ERRORS = "repeated_errors"
KIND_NARRATION_LOOP = "narration_loop"
KIND_DEGENERATE_OUTPUT = "degenerate_output"

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class LoopSignal:
    """A detected repetition, plus how many times we have already intervened."""

    kind: str
    detail: str
    nudge_count: int = 0

    @property
    def should_force_answer(self) -> bool:
        """True once nudging has failed and we should withdraw the tools."""
        return self.nudge_count >= LOOP_DETECTION_MAX_NUDGES


@dataclass
class _Turn:
    """One assistant turn, reduced to what the detector needs to compare."""

    tool_signature: str
    narration: str
    all_tools_errored: bool


def _canonical_params(params: Any) -> str:
    """Stable, order-insensitive rendering of tool arguments."""
    try:
        return json.dumps(params, sort_keys=True, default=str)
    except Exception:
        return str(params)


def signature_for_tool_calls(tool_calls: Sequence[Any]) -> str:
    """Hash the set of tool calls made in one assistant turn.

    Order within a parallel batch is not meaningful, so the parts are sorted:
    ``[a, b]`` and ``[b, a]`` are the same turn. Returns "" when the turn made
    no tool calls.
    """
    parts: List[str] = []
    for call in tool_calls or []:
        function = getattr(call, "function", None)
        if function is None and isinstance(call, dict):
            function = call.get("function")
        if function is None:
            continue
        name = (
            getattr(function, "name", None)
            if not isinstance(function, dict)
            else function.get("name")
        )
        arguments = (
            getattr(function, "arguments", None)
            if not isinstance(function, dict)
            else function.get("arguments")
        )
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except Exception:
            parsed = arguments
        parts.append(f"{name}({_canonical_params(parsed)})")

    if not parts:
        return ""
    joined = "\n".join(sorted(parts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _normalize_text(text: Optional[str]) -> str:
    """Lowercase and collapse to bare words so paraphrases compare sensibly."""
    if not text:
        return ""
    return " ".join(_WORD_RE.findall(text.lower()))


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def degenerate_repetition_ratio(text: Optional[str], n: int = 8) -> float:
    """Fraction of n-grams in ``text`` that are duplicates of an earlier one.

    A healthy paragraph is near 0. Text that has collapsed into a repeated
    phrase ("...the API. Let me check the API. Let me check the API.")
    approaches 1. Returns 0 for text too short to judge.
    """
    words = _normalize_text(text).split()
    if len(words) < max(LOOP_DETECTION_DEGENERATE_MIN_WORDS, n * 2):
        return 0.0
    ngrams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    return 1.0 - (len(set(ngrams)) / len(ngrams))


class LoopDetector:
    """Sliding-window detector over consecutive assistant turns.

    One instance per agentic run. Not thread-safe; the agentic loop is
    sequential even though the tool calls inside one turn are not.
    """

    def __init__(self) -> None:
        self._turns: List[_Turn] = []
        self._nudges = 0

    @property
    def nudge_count(self) -> int:
        return self._nudges

    def record_turn(
        self,
        tool_calls: Sequence[Any],
        content: Optional[str],
        reasoning: Optional[str],
        tool_results_all_errored: bool = False,
    ) -> Optional[LoopSignal]:
        """Record one assistant turn and report a loop if this turn completes one."""
        if not LOOP_DETECTION_ENABLED:
            return None

        try:
            turn = _Turn(
                tool_signature=signature_for_tool_calls(tool_calls),
                narration=_normalize_text(
                    " ".join(part for part in (reasoning, content) if part)
                ),
                all_tools_errored=bool(tool_calls) and tool_results_all_errored,
            )
            self._turns.append(turn)
            # Keep only what the widest check needs. Every per-check threshold
            # is included: raising one above LOOP_DETECTION_WINDOW would
            # otherwise trim the history that check needs and silently disable
            # it.
            window = max(
                LOOP_DETECTION_WINDOW,
                LOOP_DETECTION_ALTERNATION_CYCLES * 2,
                LOOP_DETECTION_REPEAT_THRESHOLD,
                LOOP_DETECTION_ERROR_STREAK,
                LOOP_DETECTION_NARRATION_REPEATS,
            )
            if len(self._turns) > window:
                self._turns = self._turns[-window:]

            signal = (
                self._check_repeated_tool_calls()
                or self._check_alternating_tool_calls()
                or self._check_repeated_errors()
                or self._check_degenerate_output(content, reasoning)
                or self._check_narration_loop()
            )
        except Exception:
            # A detector bug must never take down an investigation.
            logging.error("Loop detection failed", exc_info=True)
            return None

        if signal is None:
            return None

        signal.nudge_count = self._nudges
        self._nudges += 1
        logging.warning(
            f"Repetition loop detected ({signal.kind}): {signal.detail}. "
            f"Intervention #{self._nudges}."
        )
        return signal

    def reset(self) -> None:
        """Forget the turn window after a successful course correction.

        Deliberately does NOT reset ``_nudges``. That counter is a per-run
        escalation budget, not a per-loop one: a model that loops, gets nudged,
        corrects briefly and then loops again in a different shape is still a
        model that is failing to make progress. Resetting the budget on every
        correction would let such a run alternate between looping and nudging
        forever without ever reaching the forced answer -- exactly the failure
        this detector exists to end.
        """
        self._turns = []

    # -- individual checks -------------------------------------------------

    def _check_repeated_tool_calls(self) -> Optional[LoopSignal]:
        """The same set of tool calls, N turns in a row."""
        latest = self._turns[-1]
        if not latest.tool_signature:
            return None
        streak = 0
        for turn in reversed(self._turns):
            if turn.tool_signature != latest.tool_signature:
                break
            streak += 1
        if streak >= LOOP_DETECTION_REPEAT_THRESHOLD:
            return LoopSignal(
                kind=KIND_REPEATED_TOOL_CALLS,
                detail=f"the same tool call(s) were issued {streak} turns in a row",
            )
        return None

    def _check_alternating_tool_calls(self) -> Optional[LoopSignal]:
        """A, B, A, B, ... for N full cycles."""
        needed = LOOP_DETECTION_ALTERNATION_CYCLES * 2
        if len(self._turns) < needed:
            return None
        recent = [t.tool_signature for t in self._turns[-needed:]]
        if any(not sig for sig in recent):
            return None
        even = set(recent[0::2])
        odd = set(recent[1::2])
        if len(even) == 1 and len(odd) == 1 and even != odd:
            return LoopSignal(
                kind=KIND_ALTERNATING_TOOL_CALLS,
                detail=(
                    f"two tool calls alternated for {LOOP_DETECTION_ALTERNATION_CYCLES} "
                    "cycles without new information"
                ),
            )
        return None

    def _check_repeated_errors(self) -> Optional[LoopSignal]:
        """Every tool call failed, N turns in a row."""
        streak = 0
        for turn in reversed(self._turns):
            if not turn.all_tools_errored:
                break
            streak += 1
        if streak >= LOOP_DETECTION_ERROR_STREAK:
            return LoopSignal(
                kind=KIND_REPEATED_ERRORS,
                detail=f"every tool call failed for {streak} turns in a row",
            )
        return None

    def _check_degenerate_output(
        self, content: Optional[str], reasoning: Optional[str]
    ) -> Optional[LoopSignal]:
        """A single completion collapsed into a repeated phrase."""
        for label, text in (("reasoning", reasoning), ("content", content)):
            ratio = degenerate_repetition_ratio(text)
            if ratio >= LOOP_DETECTION_DEGENERATE_RATIO:
                return LoopSignal(
                    kind=KIND_DEGENERATE_OUTPUT,
                    detail=(
                        f"{int(ratio * 100)}% of the model's {label} in a single "
                        "response was repeated text"
                    ),
                )
        return None

    def _check_narration_loop(self) -> Optional[LoopSignal]:
        """The model keeps saying the same thing across turns."""
        if len(self._turns) < LOOP_DETECTION_NARRATION_REPEATS:
            return None
        recent = [t.narration for t in self._turns[-LOOP_DETECTION_NARRATION_REPEATS:]]
        if any(len(text.split()) < 4 for text in recent):
            return None
        latest = recent[-1]
        if all(
            _similarity(latest, other) >= LOOP_DETECTION_NARRATION_SIMILARITY
            for other in recent[:-1]
        ):
            return LoopSignal(
                kind=KIND_NARRATION_LOOP,
                detail=(
                    f"the model restated the same intent in "
                    f"{LOOP_DETECTION_NARRATION_REPEATS} consecutive turns"
                ),
            )
        return None


_NUDGE_BY_KIND: Dict[str, str] = {
    KIND_REPEATED_TOOL_CALLS: (
        "You have issued the same tool call(s) several turns in a row and the "
        "results have not changed."
    ),
    KIND_ALTERNATING_TOOL_CALLS: (
        "You have been alternating between the same two tool calls without "
        "learning anything new."
    ),
    KIND_REPEATED_ERRORS: (
        "Every tool call in your last few turns failed. Retrying them unchanged "
        "will keep failing."
    ),
    KIND_NARRATION_LOOP: (
        "You have restated the same plan several turns in a row without acting "
        "on it differently."
    ),
    KIND_DEGENERATE_OUTPUT: (
        "Your last response collapsed into repeated text instead of making " "progress."
    ),
}


def build_loop_breaker_message(signal: LoopSignal) -> Dict[str, str]:
    """The in-band message injected into the transcript when a loop is detected.

    Sent as a user-role message so it is unambiguous to every provider (some
    OpenAI-compatible servers, vLLM included, only accept a system message as
    the first message in the conversation).
    """
    preamble = _NUDGE_BY_KIND.get(signal.kind, "You appear to be stuck in a loop.")

    if signal.should_force_answer:
        return {
            "role": "user",
            "content": (
                f"STOP. {preamble} You have already been warned about this once, "
                "and no further tool calls will be executed.\n\n"
                "Write your final answer now, using only what you have already "
                "gathered. State plainly which parts of the question you could "
                "not answer and why, rather than repeating the attempt."
            ),
        }

    return {
        "role": "user",
        "content": (
            f"{preamble} ({signal.detail}.)\n\n"
            "Do not repeat that step. Choose exactly one of the following and "
            "act on it in your next message:\n"
            "1. Use a DIFFERENT tool, or the same tool with MATERIALLY different "
            "arguments (a different resource, namespace, time range or query - "
            "not a cosmetic change).\n"
            "2. Give your final answer now, based on what you have already "
            "gathered, and say explicitly what you could not determine.\n\n"
            "Do not explain that you are changing approach - just do it."
        ),
    }


def summarize_degenerate_text(text: str, max_words: int = 400) -> str:
    """Trim text that has collapsed into repetition before it re-enters context.

    Feeding a degenerate response back to the model is what turns a one-off
    stumble into a self-reinforcing loop: the model sees five copies of "Let me
    check the API" in its own history and continues the pattern. Keeping a
    prefix preserves whatever real content came before the collapse.
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    return (
        " ".join(words[:max_words])
        + "\n\n[Holmes truncated this response: it degenerated into repeated text.]"
    )
