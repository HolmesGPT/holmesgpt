"""
Base transformer abstract class for tool output transformation.
"""

__all__ = ["BaseTransformer", "TransformerError"]

from abc import ABC, abstractmethod
from pydantic import BaseModel


class TransformerError(Exception):
    """Exception raised when transformer operations fail."""

    pass


class BaseTransformer(BaseModel, ABC):
    """
    Abstract base class for all tool output transformers.

    Transformers process tool outputs before they are returned to the LLM,
    enabling operations like summarization, filtering, or format conversion.
    """

    @abstractmethod
    def transform(self, input_text: str) -> str:
        """
        Transform the input text and return the transformed output.

        Args:
            input_text: The raw tool output to transform

        Returns:
            The transformed output text

        Raises:
            TransformerError: If transformation fails
        """
        pass

    @abstractmethod
    def should_apply(self, input_text: str) -> bool:
        """
        Determine whether this transformer should be applied to the input.

        Args:
            input_text: The raw tool output to check

        Returns:
            True if the transformer should be applied, False otherwise
        """
        pass

    @property
    def name(self) -> str:
        """
        Get the transformer name.

        Returns:
            The transformer name (class name by default)
        """
        return self.__class__.__name__
