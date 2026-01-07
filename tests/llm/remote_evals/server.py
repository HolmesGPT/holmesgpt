"""Remote eval server - exposes all Holmes evals via Braintrust remote eval API.

This module creates one Braintrust Eval() per test case and handles:
- Infrastructure management (setup once vs per-run modes)
- Running Holmes investigations
- Custom scoring using existing classifiers
"""

from pathlib import Path
from typing import Any, Dict

from braintrust import Eval, Scorer, current_span

from holmes.core.tracing import TracingFactory
from tests.llm.core_eval import evaluate_test_result, run_ask_holmes_eval
from tests.llm.remote_evals.infrastructure import infrastructure_manager
from tests.llm.utils.mock_toolset import MockGenerationConfig, MockMode
from tests.llm.utils.test_case_utils import AskHolmesTestCase, get_test_cases

TEST_CASES_FOLDER = Path(__file__).parent.parent / "fixtures" / "test_ask_holmes"


def create_correctness_scorer(test_case: AskHolmesTestCase):
    """Create Braintrust scorer from our custom evaluate_test_result().

    Args:
        test_case: Test case with expected output

    Returns:
        Braintrust Scorer function
    """

    @Scorer
    def correctness(output, expected=None):
        """Score output correctness using Holmes custom classifier.

        Args:
            output: LLM output to evaluate
            expected: Expected output (from dataset, not used - we use test_case)

        Returns:
            Score dict with score and metadata
        """
        # Use shared evaluation logic
        # Note: caplog=None for remote evals (no pytest)
        scores = evaluate_test_result(
            output=output,
            test_case=test_case,
            eval_span=current_span(),
            caplog=None,
        )

        return {
            "name": "correctness",
            "score": scores.get("correctness", 0.0),
            "metadata": {
                "expected": test_case.expected_output,
                "all_scores": scores,
            },
        }

    return correctness


def create_remote_eval(test_case: AskHolmesTestCase):
    """Create one Braintrust Eval() per test case.

    Args:
        test_case: Test case to create eval for
    """

    async def task(input: Dict[str, Any], hooks):
        """Task function executed for each eval run.

        Args:
            input: Input data from dataset (contains user_prompt)
            hooks: Braintrust hooks with parameters

        Returns:
            Holmes investigation result as string
        """
        parameters = hooks.parameters
        setup_mode = parameters.get("setup_mode", "once")

        # Handle infrastructure based on setup mode
        if setup_mode == "per_run":
            try:
                if not parameters.get("skip_setup", False):
                    await infrastructure_manager.acquire_infrastructure(test_case)

                # Run the eval
                result = await _run_eval(test_case, parameters)

                return result
            finally:
                if not parameters.get("skip_cleanup", False):
                    await infrastructure_manager.release_infrastructure(test_case)
        else:
            # "once" mode - infrastructure already setup at server start
            result = await _run_eval(test_case, parameters)
            return result

    async def _run_eval(test_case: AskHolmesTestCase, parameters: Dict[str, Any]):
        """Execute the actual Holmes evaluation.

        Args:
            test_case: Test case to evaluate
            parameters: Parameters from Braintrust UI

        Returns:
            Holmes investigation result as string
        """
        import asyncio

        model = parameters.get("model", "gpt-4.1")
        tracer = TracingFactory.create_tracer("braintrust")

        # Mock generation config for remote evals
        # Always use live tools for remote evals
        mock_config = MockGenerationConfig(
            mode=MockMode.LIVE,
            generate_on_error=False,
        )

        # Run the sync function in a thread to avoid blocking
        result = await asyncio.to_thread(
            run_ask_holmes_eval,
            test_case=test_case,
            model=model,
            tracer=tracer,
            eval_span=current_span(),
            mock_generation_config=mock_config,
            request=None,  # No pytest request in remote evals
        )

        return result.result

    # Define the eval
    Eval(
        f"Holmes: {test_case.id}",
        data=[
            {
                "input": test_case.user_prompt,
                "expected": test_case.expected_output,
            }
        ],
        task=task,
        scores=[create_correctness_scorer(test_case)],
        parameters={
            "model": {
                "type": "string",
                "description": "LLM model to use (e.g., 'gpt-4.1', 'anthropic/claude-sonnet-4-20250514')",
                "default": "gpt-4.1",
            },
            "setup_mode": {
                "type": "string",
                "description": "Infrastructure setup mode: 'once' (server start, faster) or 'per_run' (each eval, isolated)",
                "default": "once",
            },
            "skip_setup": {
                "type": "boolean",
                "description": "Skip before_test commands (only for per_run mode)",
                "default": False,
            },
            "skip_cleanup": {
                "type": "boolean",
                "description": "Skip after_test commands (only for per_run mode)",
                "default": False,
            },
        },
    )


# Load all test cases and create evals
test_cases = get_test_cases(TEST_CASES_FOLDER)
for test_case in test_cases:
    create_remote_eval(test_case)

print(f"✅ Registered {len(test_cases)} remote evals")
