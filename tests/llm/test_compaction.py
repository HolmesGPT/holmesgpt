# type: ignore
import os
from pathlib import Path
from typing import Optional

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from holmes.config import Config
from holmes.core.tracing import SpanType, TracingFactory
from holmes.core.truncation.compaction import compact_conversation_history
from tests.llm.utils.braintrust import CompactionResult, log_to_braintrust
from tests.llm.utils.classifiers import evaluate_correctness
from tests.llm.utils.iteration_utils import get_test_cases
from tests.llm.utils.property_manager import (
    handle_test_error,
    set_initial_properties,
    set_trace_properties,
)
from tests.llm.utils.test_case_utils import (
    HolmesTestCase,
    check_and_skip_test,
    create_eval_llm,
    get_models,
    load_conversation_history,
)

TEST_CASES_FOLDER = Path(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures", "compaction"))
)


def get_compaction_test_cases():
    return get_test_cases(TEST_CASES_FOLDER)


@pytest.mark.llm
@pytest.mark.parametrize("model", get_models())
@pytest.mark.parametrize("test_case", get_compaction_test_cases())
def test_compaction(
    model: str,
    test_case: HolmesTestCase,
    caplog,
    request,
    shared_test_infrastructure,  # type: ignore
):
    """Test conversation history compaction functionality.

    This test verifies that the LLM can successfully compact a conversation history
    into a concise summary while preserving key information.
    """
    # Set initial properties early so they're available even if test fails
    set_initial_properties(request, test_case, model)

    tracer = TracingFactory.create_tracer("braintrust")
    metadata = {"model": model}
    tracer.start_experiment(additional_metadata=metadata)

    compacted_history: Optional[list[dict]] = None
    error: Optional[Exception] = None
    scores: Optional[dict] = None

    try:
        with tracer.start_trace(
            name=f"{test_case.id}[{model}]", span_type=SpanType.EVAL
        ) as eval_span:
            set_trace_properties(request, eval_span)
            check_and_skip_test(test_case, request, shared_test_infrastructure)

            # Load conversation history from test case
            conversation_history = load_conversation_history(Path(test_case.folder))

            if not conversation_history:
                pytest.fail(
                    f"No conversation history found for test case {test_case.id}"
                )

            # Create LLM instance (resolves via model list when configured)
            config = Config()
            llm = create_eval_llm(model=model, tracer=tracer)

            # Print original conversation for manual review
            original_tokens = llm.count_tokens(messages=conversation_history)
            console = Console()
            console.print(
                Panel(
                    f"[bold cyan]Original conversation:[/bold cyan] {original_tokens.total_tokens} tokens",
                    expand=False,
                )
            )

            # Perform compaction
            with tracer.start_trace(
                "Compaction", span_type=SpanType.TASK
            ) as compaction_span:
                compacted_history = compact_conversation_history(
                    conversation_history, llm
                )

            # Extract the summary from compacted history
            # The compacted history should have: [optional system], [optional last user prompt], [summary message], [continuation message]
            summary_content = None
            for msg in compacted_history:
                if msg.get("role") == "assistant":
                    summary_content = msg.get("content", "")
                    break

            if not summary_content:
                pytest.fail(
                    "Compaction did not produce an assistant message with summary"
                )

            compacted_tokens = llm.count_tokens(messages=compacted_history)

            # Display compaction results for manual review
            console.print(
                Panel(
                    f"[bold green]Compacted down to[/bold green] {compacted_tokens.total_tokens} tokens "
                    f"(reduced from {original_tokens.total_tokens})",
                    expand=False,
                )
            )

            console.print("\n[bold]Compacted Summary:[/bold]")
            # Use Syntax for better formatting of the summary
            summary_syntax = Syntax(
                summary_content[:2000] + ("..." if len(summary_content) > 2000 else ""),
                "markdown",
                theme="monokai",
                word_wrap=True,
            )
            console.print(summary_syntax)

            # Evaluate the quality of the summary
            expected_output = test_case.expected_output
            if isinstance(expected_output, list):
                expected_output = "\n".join(expected_output)

            # Score the compaction result
            with tracer.start_trace(
                "Evaluation", span_type=SpanType.LLM
            ) as eval_llm_span:
                # Use classifier to score the summary quality
                # Convert expected to list format if it's a string
                expected_elements = (
                    [expected_output]
                    if isinstance(expected_output, str)
                    else expected_output
                )

                correctness_eval = evaluate_correctness(
                    expected_elements=expected_elements,
                    output=summary_content,
                    parent_span=eval_llm_span,
                    caplog=caplog,
                    evaluation_type="loose",
                )

                compression_ratio = (
                    original_tokens.total_tokens - compacted_tokens.total_tokens
                ) / original_tokens.total_tokens
                scores = {
                    "correctness": correctness_eval.score,
                    "compression_ratio": compression_ratio,
                }

                # Store metadata separately (not as scores since they're not 0-1)
                metadata_dict = {
                    "original_tokens": original_tokens.model_dump(),
                    "compacted_tokens": compacted_tokens.model_dump(),
                }

            # Create result object for Braintrust logging
            result = CompactionResult(
                result=summary_content,
                original_tokens=original_tokens,
                compacted_tokens=compacted_tokens,
                compression_ratio=compression_ratio,
            )

            # Store results in user properties for reporting
            request.node.user_properties.append(("output", summary_content))
            request.node.user_properties.append(("scores", scores))
            request.node.user_properties.append(("metadata", metadata_dict))
            request.node.user_properties.append(
                ("compression_ratio", compression_ratio)
            )
            request.node.user_properties.append(
                ("original_tokens", original_tokens.total_tokens)
            )
            request.node.user_properties.append(
                ("compacted_tokens", compacted_tokens.total_tokens)
            )

            # Log to Braintrust using the shared function
            if eval_span:
                log_to_braintrust(
                    eval_span=eval_span,
                    test_case=test_case,
                    model=model,
                    result=result,
                    scores=scores,
                )

    except Exception as e:
        error = e
        handle_test_error(
            request=request,
            error=e,
            eval_span=eval_span if "eval_span" in locals() else None,
            test_case=test_case,
            model=model,
            result=None,
            mock_generation_config=None,
        )
        raise

    # Verify the test passed
    assert scores is not None, "Scores were not calculated"
    assert (
        int(scores.get("correctness", 0)) == 1
    ), f"Test {test_case.id} failed (score: {scores.get('correctness', 0)})\n\nSummary: {summary_content[:500]}...\n\nExpected: {expected_output}"
