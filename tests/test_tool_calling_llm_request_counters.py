from unittest.mock import MagicMock, patch

from holmes.core import request_counters
from holmes.core.request_counters import RequestCounters, bind_request_counters
from tests.test_tool_calling_llm import (  # noqa: F401 - fixtures
    LIMIT_PATCH,
    _make_context_limiter_passthrough,
    _make_llm_response,
    _make_mock_tool_call,
    _make_tool_call_result,
    make_ai,
    mock_llm,
    mock_tool_executor,
)


@patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
def test_parallel_tools_see_request_counters(_mock_limit, make_ai, mock_llm):  # noqa: F811
    tc1 = _make_mock_tool_call(tool_call_id="tc_a")
    tc2 = _make_mock_tool_call(tool_call_id="tc_b")
    mock_llm.completion.side_effect = [
        _make_llm_response(content="Checking", tool_calls=[tc1, tc2]),
        _make_llm_response(content="Done", tool_calls=None),
    ]

    def invoke(tool_to_call, **_kwargs):
        request_counters.increment("datadog_calls")
        return _make_tool_call_result(tool_call_id=tool_to_call.id)

    ai = make_ai()
    ai._invoke_llm_tool_call = MagicMock(side_effect=invoke)
    counters = RequestCounters()

    with bind_request_counters(counters):
        ai.call([{"role": "user", "content": "Show all"}])

    assert counters.snapshot() == {"datadog_calls": 2}
