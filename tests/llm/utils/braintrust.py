# TODO: we can remove most of this now and just use tracing.py
import logging
import os
from typing import Any, List, Optional, Union

import braintrust
from braintrust import Dataset, Experiment, ReadonlyExperiment, Span
from pydantic import BaseModel

from holmes.core.llm import TokenCountMetadata
from holmes.core.tracing import (
    BRAINTRUST_API_KEY,
    BRAINTRUST_ORG,
    BRAINTRUST_PROJECT,
    DummySpan,
    get_experiment_name,
    get_machine_state_tags,
)
from tests.llm.utils.test_case_utils import HolmesTestCase  # type: ignore

braintrust_enabled = False
if BRAINTRUST_API_KEY:
    braintrust_enabled = True


class CompactionResult(BaseModel):
    """Result wrapper for compaction tests to use with log_to_braintrust."""

    result: str  # The summary content
    original_tokens: TokenCountMetadata
    compacted_tokens: TokenCountMetadata
    compression_ratio: float


def find_dataset_row_by_test_case(dataset: Dataset, test_case: HolmesTestCase):
    for row in dataset:
        if row.get("id") == test_case.id:
            return row
    return None


def pop_test_case(
    test_cases: List[HolmesTestCase], id: str
) -> Optional[HolmesTestCase]:
    for test_case in test_cases:
        if test_case.id == id:
            test_cases.remove(test_case)
            return test_case

    return None


def pop_matching_test_case_if_exists(
    test_cases: List[HolmesTestCase], item: Any
) -> Optional[HolmesTestCase]:
    """
    This function is expected to mutate the test_cases list then
    remove the matching test case from the list and return it
    """

    test_case_id = item.get("id")
    return pop_test_case(test_cases, test_case_id)


class BraintrustEvalHelper:
    def __init__(self, project_name: str, dataset_name: str) -> None:
        self.project_name = project_name
        self.dataset_name = dataset_name
        self.dataset = None
        if braintrust_enabled:
            self.dataset = braintrust.init_dataset(
                project=project_name, name=dataset_name
            )
        self.experiment = None

    def upload_test_cases(self, test_cases: List[HolmesTestCase]):
        if not self.dataset:
            # braintrust is disabled
            return

        logging.info(f"Uploading f{len(test_cases)} test cases to braintrust")
        logging.info(f"Found dataset: {self.dataset.summarize()}")

        for item in self.dataset:
            test_case = pop_matching_test_case_if_exists(test_cases, item)
            if not test_case:
                self.dataset.delete(item.get("id"))  # type: ignore
                continue

            logging.info(f"Updating dataset item f{test_case.id}")
            # update the existing dataset item
            self.dataset.update(
                id=test_case.id,
                input=input,
                expected=test_case.expected_output,
                metadata={"test_case": test_case.model_dump()},
                tags=[],
            )

        for test_case in test_cases:
            logging.info(f"Creating dataset item f{test_case.id}")
            self.dataset.insert(
                id=test_case.id,
                input=input,
                expected=test_case.expected_output,
                metadata={"test_case": test_case.model_dump()},
                tags=[],
            )

        logging.info(self.dataset.summarize())

    def resolve_dataset_item(self, test_case: HolmesTestCase) -> Optional[Any]:
        if not self.dataset:
            # braintrust is disabled
            return None
        return find_dataset_row_by_test_case(self.dataset, test_case)

    # TODO: remove and use BraintrustTracer instead
    def start_evaluation(
        self, experiment_name: str, name: str
    ) -> Union[Span, DummySpan]:
        if not self.dataset:
            # braintrust is disabled
            return DummySpan()
        if not self.experiment:
            experiment: Experiment | ReadonlyExperiment = braintrust.init(
                project=self.project_name,
                experiment=experiment_name,
                dataset=self.dataset,
                open=False,
                update=True,
                metadata=get_machine_state_tags(),
            )

            if isinstance(
                experiment, ReadonlyExperiment
            ):  # Ensures type checker knows this is a writable experiment
                raise Exception(
                    "Experiment must be writable. The above options open=False and update=True ensure this is the case so this exception should never be raised"
                )
            self.experiment = experiment  # type: ignore

        # Create the span directly from experiment (tests manage their own spans)
        if self.experiment:
            self._root_span = self.experiment.start_span(name=name)
            return self._root_span
        else:
            return DummySpan()

    def end_evaluation(
        self,
        input: str,
        output: str,
        expected: str,
        id: str,
        scores: dict[str, Any],
        prompt: Optional[str],
        tags: Optional[list[str]] = None,
    ):
        if not self.dataset:
            # braintrust is disabled
            return
        if not self.experiment:
            raise Exception("start_evaluation() must be called before end_evaluation()")

        self._root_span.log(
            input=input,
            output=output,
            expected=expected,
            dataset_record_id=id,
            scores=scores,
            metadata={"system_prompt": prompt},
            tags=tags,
        )
        self._root_span.end()
        self.experiment.flush()


def get_dataset_name(test_suite: str):
    system_metadata = get_machine_state_tags()
    return f"{test_suite}:{system_metadata.get('branch', 'unknown_branch')}"


def log_to_braintrust(
    eval_span,
    test_case: HolmesTestCase,
    model: str,
    result: Optional[Any] = None,  # Can be LLMResult or InvestigationResult
    scores: Optional[dict] = None,
    error: Optional[Exception] = None,
    mock_generation_config: Optional[Any] = None,
) -> None:
    """Shared function to log evaluation data to Braintrust.

    Args:
        eval_span: The Braintrust evaluation span
        test_case: The test case being evaluated (AskHolmesTestCase or InvestigateTestCase)
        model: The model being tested
        result: The result object (LLMResult for ask, InvestigationResult for investigate)
        scores: Dictionary of scores (e.g., correctness)
        error: Exception if the test failed
        mock_generation_config: Mock configuration for additional context
    """
    from tests.llm.utils.test_case_utils import AskHolmesTestCase, InvestigateTestCase

    # Prepare tags
    tags = (test_case.tags or []).copy()
    tags.append(f"model:{model}")

    # Determine output based on test type and error state
    if error:
        if hasattr(
            result, "result"
        ):  # AskHolmesTestCase with LLMResult or CompactionResult
            output = result.result if result else str(error)
        elif hasattr(
            result, "analysis"
        ):  # InvestigateTestCase with InvestigationResult
            output = result.analysis if result else str(error)
        else:
            output = str(error)
        scores = scores or {}
    else:
        if hasattr(
            result, "result"
        ):  # AskHolmesTestCase with LLMResult or CompactionResult
            output = result.result if result else ""
        elif hasattr(
            result, "analysis"
        ):  # InvestigateTestCase with InvestigationResult
            output = result.analysis if result else ""
        else:
            output = ""

    # Get prompt/system prompt for ask tests
    prompt = None
    if isinstance(test_case, AskHolmesTestCase):
        if (
            result
            and hasattr(result, "messages")
            and result.messages
            and len(result.messages) > 0
        ):
            prompt = result.messages[0]["content"]
        elif result and hasattr(result, "prompt"):
            prompt = result.prompt

    # Build comprehensive metadata
    # Extract base test case ID without variant suffix (e.g., "91a_datadog[0]" -> "91a_datadog")
    base_test_id = test_case.id.split("[")[0] if "[" in test_case.id else test_case.id
    metadata: dict[str, Any] = {
        "model": model,
        "eval_id": base_test_id,  # Base test case ID without variant suffix
        "test_id": test_case.id,  # Full test case ID with variant suffix if present
    }

    # Add test type for ask tests
    if isinstance(test_case, AskHolmesTestCase):
        metadata["test_type"] = (
            test_case.test_type or os.environ.get("ASK_HOLMES_TEST_TYPE", "cli").lower()
        )

    # Add prompt if available
    if prompt:
        metadata["system_prompt"] = prompt

    # Add execution context
    if mock_generation_config and hasattr(mock_generation_config, "mode"):
        metadata["mock_mode"] = mock_generation_config.mode.value

    # Add test configuration if present
    if hasattr(test_case, "conversation_history") and test_case.conversation_history:
        metadata["has_conversation_history"] = True
    if hasattr(test_case, "runbooks") and test_case.runbooks is not None:
        metadata["has_custom_runbooks"] = True

    # Add tool usage metrics if available
    if result and getattr(result, "tool_calls", None):
        metadata["tool_call_count"] = len(result.tool_calls)
        metadata["tools_used"] = list({tc.tool_name for tc in result.tool_calls})
        # Note: holmes_duration is logged separately directly to eval_span in ask_holmes()

    # Add compaction-specific metrics if available
    if isinstance(result, CompactionResult):
        metadata["test_type"] = "compaction"
        metadata["total_original_tokens"] = result.original_tokens.total_tokens
        metadata["total_compacted_tokens"] = result.compacted_tokens.total_tokens
        metadata["original_tokens"] = result.original_tokens.model_dump()
        metadata["compacted_tokens"] = result.compacted_tokens.model_dump()
        metadata["compression_ratio"] = result.compression_ratio

    # Add error information if present
    if error:
        metadata["error_type"] = type(error).__name__
        metadata["error_message"] = str(error)

        # Add detailed setup failure information if available
        if hasattr(error, "test_id"):  # It's a SetupFailureError
            metadata["is_setup_failure"] = True
            metadata["setup_test_id"] = error.test_id
            if hasattr(error, "output") and error.output:
                # Store full setup failure details (includes script, stdout, stderr)
                # Limit to 5000 chars to avoid huge metadata
                metadata["setup_failure_details"] = (
                    error.output[:5000] if len(error.output) > 5000 else error.output
                )

        is_mock_error = "MockDataError" in type(error).__name__ or any(
            "MockData" in base.__name__ for base in type(error).__mro__
        )
        if is_mock_error:
            metadata["is_mock_data_error"] = True

    # Determine input and expected based on test type
    if isinstance(test_case, AskHolmesTestCase):
        input_data = test_case.user_prompt
        expected = (
            test_case.expected_output
            if isinstance(test_case.expected_output, str)
            else str(test_case.expected_output)
        )
    elif isinstance(test_case, InvestigateTestCase):
        input_data = str(test_case.investigate_request)
        expected = str(test_case.expected_output)
    elif test_case.conversation_history:  # compaction test case
        from tests.llm.utils.conversation_formatter import (
            format_conversation_as_markdown,
        )

        input_data = format_conversation_as_markdown(test_case.conversation_history)
        expected = (
            test_case.expected_output
            if isinstance(test_case.expected_output, str)
            else str(test_case.expected_output)
        )
    else:
        input_data = ""
        expected = ""

    # Log to Braintrust
    eval_span.log(
        input=input_data,
        output=output,
        expected=expected,
        dataset_record_id=test_case.id,
        scores=scores or {},
        metadata=metadata,
        tags=tags,
    )


def get_braintrust_url(
    span_id: Optional[str] = None,
    root_span_id: Optional[str] = None,
) -> Optional[str]:
    """Generate Braintrust URL for a test.

    Args:
        test_suite: Either "ask_holmes" or "investigate"
        test_id: Test ID like "01"
        test_name: Test name like "how_many_pods"
        span_id: Optional span ID for direct linking
        root_span_id: Optional root span ID for direct linking

    Returns:
        Braintrust URL string, or None if Braintrust is not configured
    """
    if not BRAINTRUST_API_KEY:
        return None

    from urllib.parse import quote

    experiment_name = get_experiment_name()

    # URL encode the experiment name to handle spaces and special characters
    encoded_experiment_name = quote(experiment_name, safe="")

    # Build URL with available parameters
    url = f"https://www.braintrust.dev/app/{BRAINTRUST_ORG}/p/{BRAINTRUST_PROJECT}/experiments/{encoded_experiment_name}?c="

    # Add span IDs if available
    if span_id and root_span_id:
        # Use span_id as r parameter and root_span_id as s parameter
        url += f"&r={span_id}&s={root_span_id}"

    return url
