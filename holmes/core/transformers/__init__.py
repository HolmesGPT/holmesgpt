"""
Transformer system for processing tool outputs.

This module provides the infrastructure for transforming tool outputs
before they are passed to the LLM for analysis.
"""

from .base import BaseTransformer, TransformerError
from .registry import TransformerRegistry, registry
from .llm_summarize import LLMSummarizeTransformer
from .transformer import Transformer

# Register built-in transformers
registry.register(LLMSummarizeTransformer)

__all__ = [
    "BaseTransformer",
    "TransformerError",
    "TransformerRegistry",
    "registry",
    "LLMSummarizeTransformer",
    "Transformer",
]
