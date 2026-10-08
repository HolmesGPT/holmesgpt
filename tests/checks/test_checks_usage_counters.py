import json
from unittest.mock import MagicMock

import holmes.core.usage_recorder as recorder
from holmes.checks.checks import execute_check
from holmes.checks.models import Check, CheckMode
from holmes.core import request_counters
from holmes.core.tool_calling_llm import LLMResult
from holmes.core.usage_recorder import UsageRecorderState


def _check() -> Check:
    return Check(
        name="datadog-errors",
        query="Are there payment-api errors in Datadog?",
        timeout=30,
        mode=CheckMode.MONITOR,
        destinations=[],
    )


def _ai_bumping_counter() -> MagicMock:
    def call(*_args, **_kwargs):
        request_counters.increment("datadog_calls")
        return LLMResult(
            result=json.dumps({"passed": True, "rationale": "no errors"}),
            tool_calls=[],
            num_llm_calls=1,
            messages=[],
        )

    ai = MagicMock()
    ai.llm.model = "anthropic/claude-sonnet-4-5"
    ai.call.side_effect = call
    return ai


def test_check_records_tool_counters_in_meta(monkeypatch):
    submitted = []
    monkeypatch.setattr(
        recorder._RECORDER_EXECUTOR, "submit", lambda fn, state: submitted.append(state)
    )
    state = UsageRecorderState(
        dal=MagicMock(enabled=True),
        request_type="health_check",
        model="anthropic/claude-sonnet-4-5",
        provider="anthropic",
        is_robusta_model=False,
    )

    result = execute_check(_check(), _ai_bumping_counter(), recorder_state=state)

    assert result.status.value == "pass"
    assert submitted[0].meta == {"datadog_calls": 1}


def test_check_without_recorder_state_still_runs():
    result = execute_check(_check(), _ai_bumping_counter())

    assert result.status.value == "pass"
