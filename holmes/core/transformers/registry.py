"""
Transformer registry for managing available transformers.
"""

from typing import Dict, Type, Optional, Any, List
from .base import BaseTransformer, TransformerError


class TransformerRegistry:
    """
    Registry for managing transformer types and creating transformer instances.

    This registry provides a centralized way to register transformer classes
    and create instances based on configuration.
    """

    def __init__(self):
        self._transformers: Dict[str, Type[BaseTransformer]] = {}

    def register(self, transformer_class: Type[BaseTransformer]) -> None:
        """
        Register a transformer class, using the transformer's name property.

        Args:
            transformer_class: The transformer class to register

        Raises:
            ValueError: If name is already registered or transformer_class is invalid
        """
        if not issubclass(transformer_class, BaseTransformer):
            raise ValueError(
                f"Transformer class must inherit from BaseTransformer, got {transformer_class}"
            )

        # Get name from the transformer class
        try:
            temp_instance = transformer_class()
            name = temp_instance.name
        except Exception:
            # Fallback to class name if instantiation fails
            name = transformer_class.__name__

        if name in self._transformers:
            raise ValueError(f"Transformer '{name}' is already registered")

        self._transformers[name] = transformer_class

    def unregister(self, name: str) -> None:
        """
        Unregister a transformer by name.

        Args:
            name: The name of the transformer to unregister

        Raises:
            KeyError: If transformer name is not registered
        """
        if name not in self._transformers:
            raise KeyError(f"Transformer '{name}' is not registered")

        del self._transformers[name]

    def create_transformer(
        self, name: str, config: Optional[Dict[str, Any]] = None
    ) -> BaseTransformer:
        """
        Create a transformer instance by name.

        Args:
            name: The name of the transformer to create
            config: Optional configuration for the transformer

        Returns:
            A new transformer instance

        Raises:
            KeyError: If transformer name is not registered
            TransformerError: If transformer creation fails
        """
        if name not in self._transformers:
            raise KeyError(f"Transformer '{name}' is not registered")

        transformer_class = self._transformers[name]

        try:
            # Handle both old-style dict config and new Pydantic models
            if config is None:
                return transformer_class()
            else:
                # For Pydantic models, pass config as keyword arguments
                return transformer_class(**config)
        except Exception as e:
            raise TransformerError(f"Failed to create transformer '{name}': {e}") from e

    def is_registered(self, name: str) -> bool:
        """
        Check if a transformer is registered.

        Args:
            name: The name to check

        Returns:
            True if the transformer is registered, False otherwise
        """
        return name in self._transformers

    def list_transformers(self) -> List[str]:
        """
        Get a list of all registered transformer names.

        Returns:
            List of registered transformer names
        """
        return list(self._transformers.keys())

    def clear(self) -> None:
        """Clear all registered transformers."""
        self._transformers.clear()


# Global transformer registry instance
registry = TransformerRegistry()
