import os
import json


def is_run_live_enabled() -> bool:
    """
    Check if RUN_LIVE environment variable is set to enable live test execution.

    This centralizes the logic for checking RUN_LIVE across all test files.
    Uses the same json.loads() approach as holmes/common/env_vars.py for consistency.

    Returns:
        True if RUN_LIVE is set to a truthy value, False otherwise.
    """
    env_value = os.environ.get("RUN_LIVE")
    if env_value is None:
        return False

    try:
        # Use json.loads for consistent boolean parsing (handles true/false/1/0)
        return json.loads(env_value.lower())
    except (json.JSONDecodeError, AttributeError):
        # If not valid JSON, check for common string representations
        return env_value.strip().lower() in ("true", "1", "t", "yes", "y")
