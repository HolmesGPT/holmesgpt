# type: ignore
import os
import time
from datetime import datetime
from os import path
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from rich.console import Console

from holmes.config import Config
from holmes.core.conversations import build_chat_messages
from holmes.core.models import ChatRequest
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.tracing import SpanType, TracingFactory
from holmes.plugins.runbooks import RunbookCatalog, load_runbook_catalog
from tests.llm.utils.braintrust import log_to_braintrust
from tests.llm.utils.commands import set_test_env_vars
from tests.llm.utils.iteration_utils import get_test_cases
from tests.llm.utils.mock_dal import load_mock_dal
from tests.llm.utils.mock_toolset import (
    MockGenerationConfig,
    MockToolsetManager,
    check_for_mock_errors,
)
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
    create_eval_llm,
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
    with eval_span.start_span(
        "Initialize Toolsets",
        type=SpanType.TASK.value,
    ):
        toolset_manager = MockToolsetManager(
            test_case_folder=test_case.folder,
            mock_generation_config=mock_generation_config,
            request=request,
            mock_policy=test_case.mock_policy,
            mock_overrides=test_case.mock_overrides,
            allow_toolset_failures=getattr(test_case, "allow_toolset_failures", False),
        )

    tool_executor = ToolExecutor(toolset_manager.toolsets)
    enabled_toolsets = [t.name for t in tool_executor.enabled_toolsets]
    print(
        f"\n🛠️  ENABLED TOOLSETS ({len(enabled_toolsets)}):", ", ".join(enabled_toolsets)
    )

    ai = ToolCallingLLM(
        tool_executor=tool_executor,
        max_steps=40,
        llm=create_eval_llm(model=model, tracer=tracer),
    )

    test_type = (
        test_case.test_type or os.environ.get("ASK_HOLMES_TEST_TYPE", "cli").lower()
    )
    if test_type == "cli":
        if test_case.conversation_history:
            pytest.skip("CLI mode does not support conversation history tests")
        else:
            console = Console()
            if test_case.runbooks is None:
                runbooks = load_runbook_catalog()
            elif test_case.runbooks == {}:
                runbooks = None
            else:
                try:
                    runbooks = RunbookCatalog(**test_case.runbooks)
                except Exception as e:
                    raise ValueError(
                        f"Failed to convert runbooks dict to RunbookCatalog: {e}. "
                        f"Expected format: {{'catalog': [...]}}, got: {test_case.runbooks}"
                    ) from e
            messages = build_initial_ask_messages(
                console,
                test_case.user_prompt,
                None,
                ai.tool_executor,
                runbooks,
            )
    else:
        chat_request = ChatRequest(ask=test_case.user_prompt)
        config = Config()
        if test_case.cluster_name:
            config.cluster_name = test_case.cluster_name

        mock_dal = load_mock_dal(
            Path(test_case.folder), generate_mocks=False, initialize_base=False
        )
        runbooks = load_runbook_catalog(mock_dal)
        global_instructions = mock_dal.get_global_instructions_for_account()

        messages = build_chat_messages(
            ask=chat_request.ask,
            conversation_history=test_case.conversation_history,
            ai=ai,
            config=config,
            global_instructions=global_instructions,
            runbooks=runbooks,
        )

    # Create LLM completion trace within current context
    with tracer.start_trace("Holmes Run", span_type=SpanType.TASK) as llm_span:
        start_time = time.time()
        result = ai.messages_call(messages=messages, trace_span=llm_span)
        holmes_duration = time.time() - start_time
        # Log duration directly to eval_span
        eval_span.log(metadata={"holmes_duration": holmes_duration})
        # Store metrics in user_properties for GitHub report
        if request:
            request.node.user_properties.append(("holmes_duration", holmes_duration))
            if result.num_llm_calls is not None:
                request.node.user_properties.append(
                    ("num_llm_calls", result.num_llm_calls)
                )
            if result.tool_calls is not None:
                request.node.user_properties.append(
                    ("tool_call_count", len(result.tool_calls))
                )

    # Check for any mock errors that occurred during tool execution
    # This will raise an exception if any mock data errors happened
    if request:
        check_for_mock_errors(request)

    return result
