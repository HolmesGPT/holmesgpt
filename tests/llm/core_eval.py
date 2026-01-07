"""Shared evaluation logic used by both pytest tests and remote evals.

This module contains core Holmes evaluation functions that are reused by:
- tests/llm/test_ask_holmes.py (pytest tests)
- tests/llm/remote_evals/server.py (remote Braintrust evals)
"""

import os
import time
from pathlib import Path
from typing import Optional, Union

from rich.console import Console

from holmes.config import Config
from holmes.core.conversations import build_chat_messages
from holmes.core.models import ChatRequest
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.tracing import SpanType
from holmes.plugins.runbooks import RunbookCatalog, load_runbook_catalog
from tests.llm.utils.mock_dal import load_mock_dal
from tests.llm.utils.mock_toolset import (
    MockGenerationConfig,
    MockToolsetManager,
    check_for_mock_errors,
)
from tests.llm.utils.test_case_utils import AskHolmesTestCase, create_eval_llm


def run_ask_holmes_eval(
    test_case: AskHolmesTestCase,
    model: str,
    tracer,
    eval_span,
    mock_generation_config: MockGenerationConfig,
    request=None,  # Optional, only for pytest
) -> LLMResult:
    """Core Holmes evaluation logic - shared between pytest and remote evals.

    Args:
        test_case: Test case configuration
        model: LLM model to use
        tracer: Tracer instance (BraintrustTracer or DummyTracer)
        eval_span: Parent span for tracing
        mock_generation_config: Mock toolset configuration
        request: Pytest request fixture (None for remote evals)

    Returns:
        LLMResult with the investigation result
    """
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
            if request:  # Only skip if running in pytest
                import pytest

                pytest.skip("CLI mode does not support conversation history tests")
            else:
                raise ValueError("CLI mode does not support conversation history tests")
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
        # Store metrics in user_properties for GitHub report (pytest only)
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


def evaluate_test_result(
    output: str,
    test_case: AskHolmesTestCase,
    eval_span,
    caplog=None,  # Optional, only for pytest
) -> dict:
    """Evaluate test output using custom scorers.

    Args:
        output: The LLM's output to evaluate
        test_case: Test case with expected output
        eval_span: Parent span for tracing
        caplog: Pytest caplog fixture (None for remote evals)

    Returns:
        Dictionary with scores: {"correctness": 0.0-1.0, ...}
    """
    from tests.llm.utils.classifiers import evaluate_correctness, evaluate_sections

    scores = {}

    # Evaluate correctness
    if isinstance(test_case.expected_output, list):
        expected = test_case.expected_output
    else:
        expected = [test_case.expected_output]

    correctness_result = evaluate_correctness(
        expected_elements=expected,
        output=output,
        parent_span=eval_span,
        caplog=caplog,  # Will be None for remote evals
        evaluation_type=getattr(test_case, "evaluation_type", "strict"),
    )
    scores["correctness"] = correctness_result.score

    # Evaluate sections if specified
    if hasattr(test_case, "expected_sections") and test_case.expected_sections:
        sections_result = evaluate_sections(
            sections=test_case.expected_sections,
            output=output,
            parent_span=eval_span,
        )
        scores["sections"] = sections_result.score

    return scores
