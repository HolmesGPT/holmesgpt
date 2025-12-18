"""
LLM Summarize Transformer for fast model summarization of large tool outputs.
"""

import logging
from typing import Optional, ClassVar
from pydantic import Field, PrivateAttr, StrictStr

from .base import BaseTransformer, TransformerError
from ..llm import DefaultLLM, LLM

logger = logging.getLogger(__name__)


class LLMSummarizeTransformer(BaseTransformer):
    """
    Transformer that uses a fast LLM model to summarize large tool outputs.

    This transformer applies summarization when:
    1. A fast model is available
    2. The input length exceeds the configured threshold

    Configuration options:
    - input_threshold: Minimum input length to trigger summarization (default: 1000)
    - prompt: Custom prompt template for summarization (optional)
    - fast_model: Fast model name for summarization (e.g., "gpt-4o-mini")
    - api_key: API key for the fast model (optional, uses default if not provided)
    """

    DEFAULT_PROMPT: ClassVar[str] = """Summarize this operational data focusing on:
- What needs attention or immediate action
- Group similar entries into a single line and description
- Make sure to mention outliers, errors, and non-standard patterns
- List normal/healthy patterns as aggregate descriptions
- When listing problematic entries, also try to use aggregate descriptions when possible
- When possible, mention exact keywords, IDs, or patterns so the user can filter/search the original data and drill down on the parts they care about (extraction over abstraction)"""

    # Pydantic fields with validation
    input_threshold: int = Field(
        default=1000, ge=0, description="Minimum input length to trigger summarization"
    )
    prompt: Optional[StrictStr] = Field(
        default=None,
        min_length=1,
        description="Custom prompt template for summarization",
    )
    fast_model: Optional[StrictStr] = Field(
        default=None,
        min_length=1,
        description="Fast model name for summarization (e.g., 'gpt-4o-mini')",
    )
    global_fast_model: Optional[StrictStr] = Field(
        default=None,
        min_length=1,
        description="Global fast model name fallback when fast_model is not set",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the fast model (optional, uses default if not provided)",
    )

    # Private attribute for the LLM instance (not serialized)
    _fast_llm: Optional[LLM] = PrivateAttr(default=None)

    def model_post_init(self, __context) -> None:
        """Initialize the fast LLM instance after model validation."""
        logger = logging.getLogger(__name__)

        self._fast_llm = None

        # Determine which fast model to use: fast_model takes precedence over global_fast_model
        effective_fast_model = self.fast_model or self.global_fast_model

        logger.debug(
            f"LLMSummarizeTransformer initialization: fast_model='{self.fast_model}', global_fast_model='{self.global_fast_model}', effective='{effective_fast_model}'"
        )

        # Create fast LLM instance if a fast model is available
        if effective_fast_model:
            try:
                self._fast_llm = DefaultLLM(effective_fast_model, self.api_key)
                logger.info(
                    f"Created fast LLM instance with model: {effective_fast_model}"
                )
            except Exception as e:
                logger.warning(f"Failed to create fast LLM instance: {e}")
                self._fast_llm = None
        else:
            logger.debug(
                "No fast model configured (neither fast_model nor global_fast_model)"
            )

    def should_apply(self, input_text: str) -> bool:
        """
        Determine if summarization should be applied to the input.

        Args:
            input_text: The tool output to check

        Returns:
            True if summarization should be applied, False otherwise
        """
        logger = logging.getLogger(__name__)

        # Skip if no fast model is configured
        if self._fast_llm is None:
            logger.debug(
                f"Skipping summarization: no fast model configured (fast_model='{self.fast_model}', global_fast_model='{self.global_fast_model}')"
            )
            return False

        # Check if input exceeds threshold
        input_length = len(input_text)

        if input_length <= self.input_threshold:
            logger.debug(
                f"Skipping summarization: input length {input_length} <= threshold {self.input_threshold}"
            )
            return False

        logger.debug(
            f"Applying summarization: input length {input_length} > threshold {self.input_threshold}"
        )
        return True

    def transform(self, input_text: str) -> str:
        """
        Transform the input text by summarizing it with the fast model.

        Args:
            input_text: The tool output to summarize

        Returns:
            Summarized text

        Raises:
            TransformerError: If summarization fails
        """
        if self._fast_llm is None:
            raise TransformerError("Cannot transform: no fast model configured")

        try:
            # Get the prompt to use
            prompt = self.prompt or self.DEFAULT_PROMPT

            # Construct the full prompt with the content
            full_prompt = f"{prompt}\n\nContent to summarize:\n{input_text}"

            # Perform the summarization
            logger.debug(f"Summarizing {len(input_text)} characters with fast model")

            response = self._fast_llm.completion(
                [{"role": "user", "content": full_prompt}]
            )
            summarized_text = response.choices[0].message.content  # type: ignore

            if not summarized_text or not summarized_text.strip():
                raise TransformerError("Fast model returned empty summary")

            logger.debug(
                f"Summarization complete: {len(input_text)} -> {len(summarized_text)} characters"
            )

            return summarized_text.strip()

        except Exception as e:
            error_msg = f"Failed to summarize content with fast model: {e}"
            logger.error(error_msg)
            raise TransformerError(error_msg) from e

    @property
    def name(self) -> str:
        """Get the transformer name."""
        return "llm_summarize"
