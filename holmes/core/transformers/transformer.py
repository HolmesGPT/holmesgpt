"""
Configuration class for tool transformers.
"""

import logging
from typing import Any, Dict
from pydantic import BaseModel, Field, model_validator

from .registry import registry


class Transformer(BaseModel):
    """
    Configuration for a tool transformer.

    Each transformer config specifies a transformer type and its parameters.
    This replaces the previous dict-based configuration with proper type safety.
    """

    name: str = Field(description="Name of the transformer (e.g., 'llm_summarize')")
    config: Dict[str, Any] = Field(
        default_factory=dict, description="Configuration parameters for the transformer"
    )

    @model_validator(mode="after")
    def validate_transformer(self):
        """Validate that the transformer name is known to the registry."""
        if not registry.is_registered(self.name):
            # Log warning but don't fail validation - allows for graceful degradation
            logging.warning(f"Transformer '{self.name}' is not registered")
        return self
