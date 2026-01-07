# type: ignore
from datetime import datetime
from os import path
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from holmes.core.tool_calling_llm import LLMResult
from holmes.core.tracing import TracingFactory, SpanType
from tests.llm.utils.braintrust import log_to_braintrust
from tests.llm.utils.commands import set_test_env_vars
from tests.llm.utils.iteration_utils import get_test_cases
from tests.llm.utils.mock_toolset import MockGenerationConfig
from tests.llm.utils.property_manager import (
    handle_test_error,
    set_initial_properties,
    set_trace_properties,
    update_test_results,
)
from tests.llm.utils.retry_handler import retry_on_throttle
from tests.llm.utils.test_case_utils import (
    AskHolmesTestCase,
    check_and_skip_test,
    get_models,
)

TEST_CASES_FOLDER = Path(
    path.abspath(path.join(path.dirname(__file__), "fixtures", "test_ask_holmes"))
)


def get_ask_holmes_test_cases():
    return get_test_cases(TEST_CASES_FOLDER)


@pytest.mark.llm
@pytest.mark.parametrize("model", get_models())
@pytest.mark.parametrize("test_case", get_ask_holmes_test_cases())
def test_ask_holmes(
    model: str,
    test_case: AskHolmesTestCase,
    caplog,
    request,
    mock_generation_config: MockGenerationConfig,
    shared_test_infrastructure,  # type: ignore
):
    # Set initial properties early so they're available even if test fails
    set_initial_properties(request, test_case, model)

    tracer = TracingFactory.create_tracer("braintrust")
    metadata = {"model": model}
    tracer.start_experiment(additional_metadata=metadata)

    result: Optional[LLMResult] = None

    try:
        with tracer.start_trace(
            name=f"{test_case.id}[{model}]", span_type=SpanType.EVAL
        ) as eval_span:
            set_trace_properties(request, eval_span)
            check_and_skip_test(test_case, request, shared_test_infrastructure)

            # Use contextlib.ExitStack to handle conditional context managers
            from contextlib import ExitStack

            with ExitStack() as stack:
                # Mock datetime if mocked_date is provided
                if test_case.mocked_date:
                    mocked_datetime = datetime.fromisoformat(
                        test_case.mocked_date.replace("Z", "+00:00")
                    )
                    mock_datetime = stack.enter_context(
                        patch("holmes.plugins.prompts.datetime")
                    )
                    mock_datetime.now.return_value = mocked_datetime
                    mock_datetime.side_effect = None
                    mock_datetime.configure_mock(
                        **{"now.return_value": mocked_datetime, "side_effect": None}
                    )

                # Always apply test env vars
                stack.enter_context(set_test_env_vars(test_case))

                # Run the test with retry logic
                retry_enabled = request.config.getoption(
                    "retry-on-throttle", default=True
                )
                result = retry_on_throttle(
                    ask_holmes,
                    test_case,  # positional arg
                    model,  # positional arg
                    tracer,  # positional arg
                    eval_span,  # positional arg
                    mock_generation_config,  # positional arg
                    request=request,
                    retry_enabled=retry_enabled,
                    test_id=test_case.id,
                    model=model,  # Also pass for logging in retry_handler
                )

    except Exception as e:
        handle_test_error(
            request=request,
            error=e,
            eval_span=eval_span if "eval_span" in locals() else None,
            test_case=test_case,
            model=model,
            result=result,
            mock_generation_config=mock_generation_config,
        )
        raise

    output = result.result

    scores = update_test_results(
        request=request,
        output=output,
        tools_called=[tc.description for tc in result.tool_calls]
        if result.tool_calls
        else [],
        scores=None,  # Let it calculate
        result=result,
        test_case=test_case,
        eval_span=eval_span,
        caplog=caplog,
    )

    if eval_span:
        log_to_braintrust(
            eval_span=eval_span,
            test_case=test_case,
            model=model,
            result=result,
            scores=scores,
            mock_generation_config=mock_generation_config,
        )

    # Get expected for assertion message
    expected_output = test_case.expected_output
    if isinstance(expected_output, list):
        expected_output = "\n-  ".join(expected_output)

    assert (
        int(scores.get("correctness", 0)) == 1
    ), f"Test {test_case.id} failed (score: {scores.get('correctness', 0)})\nActual: {output}\nExpected: {expected_output}"


# TODO: can this call real ask_holmes so more of the logic is captured
def ask_holmes(
    test_case: AskHolmesTestCase,
    model: str,
    tracer,
    eval_span,
    mock_generation_config,
    request=None,
) -> LLMResult:
    from tests.llm.core_eval import run_ask_holmes_eval

    return run_ask_holmes_eval(
        test_case, model, tracer, eval_span, mock_generation_config, request
    )
