# type: ignore
import os
import time
from os import path
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from holmes.config import Config
from holmes.core.investigation import investigate_issues
from holmes.core.investigation_structured_output import DEFAULT_SECTIONS
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tool_calling_llm import IssueInvestigator
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.tracing import SpanType, TracingFactory
from tests.llm.utils.braintrust import log_to_braintrust
from tests.llm.utils.classifiers import (
    evaluate_correctness,
    evaluate_sections,
)
from tests.llm.utils.commands import set_test_env_vars
from tests.llm.utils.iteration_utils import get_test_cases
from tests.llm.utils.mock_dal import MockSupabaseDal
from tests.llm.utils.mock_toolset import MockToolsetManager, check_for_mock_errors
from tests.llm.utils.property_manager import (
    handle_test_error,
    set_initial_properties,
    set_trace_properties,
    update_test_results,
)
from tests.llm.utils.retry_handler import retry_on_throttle
from tests.llm.utils.test_case_utils import (
    InvestigateTestCase,
    check_and_skip_test,
    get_models,
)

TEST_CASES_FOLDER = Path(
    path.abspath(path.join(path.dirname(__file__), "fixtures", "test_investigate"))
)


class MockConfig(Config):
    def __init__(self, test_case: InvestigateTestCase, tracer, mock_generation_config):
        super().__init__()
        self._test_case = test_case
        self._tracer = tracer
        self._mock_generation_config = mock_generation_config
        self._cached_tool_executor: Optional[ToolExecutor] = None

    def create_tool_executor(self, dal: Optional[SupabaseDal]) -> ToolExecutor:
        if not self._cached_tool_executor:
            mock = MockToolsetManager(
                test_case_folder=self._test_case.folder,
                mock_generation_config=self._mock_generation_config,
                mock_policy=self._test_case.mock_policy,
                mock_overrides=getattr(self._test_case, "mock_overrides", None),
            )

            # With the new file-based mock system, mocks are loaded from disk automatically
            # No need to call mock_tool() anymore
            self._cached_tool_executor = ToolExecutor(mock.toolsets)
        return self._cached_tool_executor

    def create_issue_investigator(
        self,
        dal: Optional[SupabaseDal] = None,
        model: Optional[str] = None,
        tracer=None,
    ) -> IssueInvestigator:
        # Use our tracer instead of the passed one
        return super().create_issue_investigator(
            dal=dal, model=model, tracer=self._tracer
        )


def get_investigate_test_cases():
    return get_test_cases(TEST_CASES_FOLDER)


@pytest.mark.llm
@pytest.mark.parametrize("model", get_models())
@pytest.mark.parametrize("test_case", get_investigate_test_cases())
def test_investigate(
    model: str,
    test_case: InvestigateTestCase,
    caplog,
    request,
    mock_generation_config,
    shared_test_infrastructure,  # type: ignore
):
    # Set initial properties early so they're available even if test fails
    set_initial_properties(request, test_case, model)

    tracer = TracingFactory.create_tracer("braintrust")
    config = MockConfig(test_case, tracer, mock_generation_config)
    config.model = model
    metadata = {"model": model}
    tracer.start_experiment(additional_metadata=metadata)

    mock_dal = MockSupabaseDal(
        test_case_folder=Path(test_case.folder),
        generate_mocks=mock_generation_config.generate_mocks,
        issue_data=test_case.issue_data,
        issues_metadata=None,
        resource_instructions=test_case.resource_instructions,
    )

    input = test_case.investigate_request
    expected = test_case.expected_output
    result = None
    output = None
    scores = {}

    investigate_request = test_case.investigate_request
    if not investigate_request.sections:
        investigate_request.sections = DEFAULT_SECTIONS

    try:
        with patch.dict(
            os.environ, {"HOLMES_STRUCTURED_OUTPUT_CONVERSION_FEATURE_FLAG": "False"}
        ):
            with tracer.start_trace(
                name=f"{test_case.id}[{model}]", span_type=SpanType.EVAL
            ) as eval_span:
                set_trace_properties(request, eval_span)
                check_and_skip_test(test_case, request, shared_test_infrastructure)

                with set_test_env_vars(test_case):
                    with eval_span.start_span(
                        "Caching tools executor for create_issue_investigator",
                        type=SpanType.TASK.value,
                    ):
                        config.create_tool_executor(mock_dal)
                    with eval_span.start_span(
                        "Holmes Run", type=SpanType.TASK.value
                    ) as holmes_span:
                        start_time = time.time()
                        retry_enabled = request.config.getoption(
                            "retry-on-throttle", default=True
                        )
                        result = retry_on_throttle(
                            investigate_issues,
                            investigate_request=investigate_request,
                            config=config,
                            dal=mock_dal,
                            trace_span=holmes_span,
                            retry_enabled=retry_enabled,
                            test_id=test_case.id,
                            model=model,
                        )
                        holmes_duration = time.time() - start_time
                    # Log duration directly to eval_span
                    eval_span.log(metadata={"holmes_duration": holmes_duration})
                    # Store metrics in user_properties for GitHub report
                    request.node.user_properties.append(
                        ("holmes_duration", holmes_duration)
                    )
                    if result and result.num_llm_calls is not None:
                        request.node.user_properties.append(
                            ("num_llm_calls", result.num_llm_calls)
                        )
                    if result and result.tool_calls is not None:
                        request.node.user_properties.append(
                            ("tool_call_count", len(result.tool_calls))
                        )

                # Check for any mock errors that occurred during tool execution
                # This will raise an exception if any mock data errors happened
                check_for_mock_errors(request)

                # Evaluate and log results inside the span context
                assert result, "No result returned by investigate_issues()"

                output = result.analysis

                correctness_eval = evaluate_correctness(
                    output=output,
                    expected_elements=expected,
                    parent_span=eval_span,
                    caplog=caplog,
                    evaluation_type="strict",
                )
                scores["correctness"] = correctness_eval.score

                if test_case.expected_sections:
                    sections = {
                        key: bool(value)
                        for key, value in test_case.expected_sections.items()
                    }
                    sections_eval = evaluate_sections(
                        sections=sections, output=output, parent_span=eval_span
                    )
                    scores["sections"] = sections_eval.score

                # Log evaluation results to the span
                log_to_braintrust(
                    eval_span=eval_span,
                    test_case=test_case,
                    model=model,
                    result=result,
                    scores=scores,
                    mock_generation_config=mock_generation_config,
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

    tools_called = [t.tool_name for t in result.tool_calls] if result.tool_calls else []
    update_test_results(request, output, tools_called, scores, result)

    assert result.sections, "Missing sections"
    assert (
        len(result.sections) >= len(investigate_request.sections)
    ), f"Received {len(result.sections)} sections but expected {len(investigate_request.sections)}. Received: {result.sections.keys()}"
    for expected_section_title in investigate_request.sections:
        assert (
            expected_section_title in result.sections
        ), f"Expected title {expected_section_title} in sections"

    assert (
        int(scores.get("correctness", 0)) == 1
    ), f"Test {test_case.id} failed (score: {scores.get('correctness', 0)})"

    if test_case.expected_sections:
        for (
            expected_section_title,
            expected_section_array_content,
        ) in test_case.expected_sections.items():
            if expected_section_array_content:
                assert (
                    expected_section_title in result.sections
                ), f"Expected to see section [{expected_section_title}] in result but that section is missing"
                for expected_content in expected_section_array_content:
                    assert (
                        expected_content
                        in result.sections.get(expected_section_title, "")
                    ), f"Expected to see content [{expected_content}] in section [{expected_section_title}] but could not find such content"
