import base64
import logging
import os
from typing import Any, Optional, Union

from braintrust import Attachment
from pydantic import BaseModel

from holmes.core.llm import ContextWindowUsage
from holmes.core.tool_calling_llm import LLMResult
from holmes.core.tracing import (
    BRAINTRUST_API_KEY,
    BRAINTRUST_ORG,
    BRAINTRUST_PROJECT,
    get_experiment_name,
)
from tests.llm.utils.test_case_utils import AskHolmesTestCase, HolmesTestCase  # type: ignore


class CompactionResult(BaseModel):
    """Result wrapper for compaction tests to use with log_to_braintrust."""

    result: str  # The summary content
    original_tokens: ContextWindowUsage
    compacted_tokens: ContextWindowUsage
    compression_ratio: float


def log_to_braintrust(
    eval_span,
    test_case: HolmesTestCase,
    model: str,
    result: Optional[Union[LLMResult, CompactionResult]] = None,
    scores: Optional[dict] = None,
    error: Optional[Exception] = None,
) -> None:
    """Log evaluation data to Braintrust.

    Handles both ask_holmes tests (LLMResult) and compaction tests
    (CompactionResult), logging appropriate metrics for each.

    Args:
        eval_span: The Braintrust evaluation span
        test_case: The test case being evaluated
        model: The model being tested
        result: LLMResult for ask_holmes tests, CompactionResult for compaction tests
        scores: Dictionary of scores (e.g., correctness)
        error: Exception if the test failed
    """

    # Prepare tags
    tags = (test_case.tags or []).copy()
    tags.append(f"model:{model}")

    # Determine output based on test type and error state
    if error:
        if hasattr(result, "result"):
            output = result.result if result else str(error)
        else:
            output = str(error)
        scores = scores or {}
    else:
        if hasattr(result, "result"):
            output = result.result if result else ""
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
            # Find the first message with role "system"
            system_msg = next(
                (m for m in result.messages if m.get("role") == "system"),
                None
            )
            prompt = system_msg["content"] if system_msg else "<NO SYSTEM PROMPT FOUND>"

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

    # Add test configuration if present
    if hasattr(test_case, "conversation_history") and test_case.conversation_history:
        metadata["has_conversation_history"] = True
    if hasattr(test_case, "skills") and test_case.skills is not None:
        metadata["has_custom_skills"] = True

    # Add tool usage metrics if available
    if result and getattr(result, "tool_calls", None):
        metadata["tool_call_count"] = len(result.tool_calls)
        metadata["tools_used"] = list({tc.tool_name for tc in result.tool_calls})
        # Note: holmes_duration is logged separately directly to eval_span in ask_holmes()

    # Number of LLM round trips (turns), used by the PR-vs-benchmark comparison
    if result and getattr(result, "num_llm_calls", None) is not None:
        metadata["num_llm_calls"] = result.num_llm_calls

    # Add token and cost data for benchmark comparison
    if result and hasattr(result, "total_tokens"):
        metadata["total_tokens"] = result.total_tokens
        metadata["prompt_tokens"] = result.prompt_tokens
        metadata["completion_tokens"] = result.completion_tokens
        if result.cached_tokens is not None:
            metadata["cached_tokens"] = result.cached_tokens
    if result and hasattr(result, "total_cost") and result.total_cost:
        metadata["cost"] = result.total_cost

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

    # Determine input and expected based on test type
    if isinstance(test_case, AskHolmesTestCase):
        input_data = test_case.user_prompt
        expected = (
            test_case.expected_output
            if isinstance(test_case.expected_output, str)
            else str(test_case.expected_output)
        )
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

    # Collect images from tool call results as Braintrust Attachments
    tool_call_images: list[Attachment] = []
    if result and getattr(result, "tool_calls", None):
        for tc in result.tool_calls:
            if tc.result and tc.result.images:
                for img_idx, img in enumerate(tc.result.images):
                    try:
                        img_bytes = base64.b64decode(img["data"])
                        mime_type = img.get("mimeType", "image/png")
                        ext = mime_type.split("/")[-1] if "/" in mime_type else "png"
                        tool_call_images.append(
                            Attachment(
                                data=img_bytes,
                                filename=f"{tc.tool_name}_{img_idx}.{ext}",
                                content_type=mime_type,
                            )
                        )
                    except Exception:
                        logging.debug(f"Failed to create Braintrust attachment for {tc.tool_name} image {img_idx}")

    if tool_call_images:
        metadata["tool_call_images"] = tool_call_images

    # Log to Braintrust
    eval_span.log(
        input=input_data,
        output=output,
        expected=expected,
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
