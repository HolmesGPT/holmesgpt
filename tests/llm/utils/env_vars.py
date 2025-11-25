import os


def is_run_live_enabled() -> bool:
    """
    Check if RUN_LIVE environment variable is set to enable live test execution.

    Returns True by default (if not set). Returns False only if explicitly
    set to a false-like value: false, 0, f, no, n (case-insensitive).

    Returns:
        bool: True unless RUN_LIVE is explicitly set to false.
    """
    # Default to "true" if not set, then check if it's NOT a false value
    return os.environ.get("RUN_LIVE", "true").strip().lower() not in (
        "false",
        "0",
        "f",
        "no",
        "n",
    )
