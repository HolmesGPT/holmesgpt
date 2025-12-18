import fnmatch
from typing import List


def model_matches_list(model: str, model_list: List[str]) -> bool:
    """
    Check if a model matches any pattern in a list of model patterns.

    Args:
        model: The name of an LLM model (e.g., "azure/gpt", "openai/gpt-4o")
        model_list: List of model patterns that may include wildcards
                   (e.g., ["azure/*", "*/mistral", "openai/gpt-*"])

    Returns:
        True if the model matches any pattern in the list, False otherwise
    """
    for pattern in model_list:
        if fnmatch.fnmatchcase(model, pattern):
            return True
    return False
