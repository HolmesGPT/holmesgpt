"""
Configuration utility functions for HolmesGPT.
"""

from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from holmes.core.transformers import Transformer


def merge_transformers(
    base_transformers: Optional[List["Transformer"]],
    override_transformers: Optional[List["Transformer"]],
    only_merge_when_override_exists: bool = False,
) -> Optional[List["Transformer"]]:
    """
    Merge transformer configurations with intelligent field-level merging.

    Logic:
    - Override transformers take precedence for existing fields
    - Base transformers provide missing fields
    - Merge at transformer-type level (e.g., "llm_summarize")

    Args:
        base_transformers: Base transformer configurations (e.g., global transformers)
        override_transformers: Override transformer configurations (e.g., toolset transformers)
        only_merge_when_override_exists: If True, only merge when override_transformers exist.

    Returns:
        Merged transformer configuration list or None if both inputs are None/empty
    """
    if not base_transformers and not override_transformers:
        return None
    if not base_transformers:
        return override_transformers
    if not override_transformers:
        if only_merge_when_override_exists:
            return None  # Don't apply base transformers if override doesn't exist
        else:
            return base_transformers  # Original behavior: return base transformers

    # Convert lists to dicts keyed by transformer name for easier merging
    base_dict = {}
    for transformer in base_transformers:
        base_dict[transformer.name] = transformer

    override_dict = {}
    for transformer in override_transformers:
        override_dict[transformer.name] = transformer

    # Merge configurations at field level
    merged_transformers = []

    # Start with all base transformer types
    for transformer_name, base_transformer in base_dict.items():
        if transformer_name in override_dict:
            # Merge fields: override takes precedence, base provides missing fields
            override_transformer = override_dict[transformer_name]
            merged_config = dict(base_transformer.config)  # Start with base
            merged_config.update(
                override_transformer.config
            )  # Override with specific fields

            # IMPORTANT: Preserve global_fast_model from both base and override
            # This ensures our injected global_fast_model settings aren't lost during merging
            if "global_fast_model" in base_transformer.config:
                merged_config["global_fast_model"] = base_transformer.config[
                    "global_fast_model"
                ]
            if "global_fast_model" in override_transformer.config:
                merged_config["global_fast_model"] = override_transformer.config[
                    "global_fast_model"
                ]

            # Create new transformer with merged config
            from holmes.core.transformers import Transformer

            merged_transformer = Transformer(
                name=transformer_name, config=merged_config
            )
            merged_transformers.append(merged_transformer)
        else:
            # No override, use base transformer as-is
            merged_transformers.append(base_transformer)

    # Add any override-only transformer types
    for transformer_name, override_transformer in override_dict.items():
        if transformer_name not in base_dict:
            merged_transformers.append(override_transformer)

    return merged_transformers
