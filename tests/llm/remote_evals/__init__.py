"""Remote Braintrust evals for HolmesGPT.

This package exposes all Holmes evaluation tests as remote Braintrust evals.

Usage:
    # Start server with "setup once" mode (default)
    braintrust eval tests/llm/remote_evals --dev

    # Start server with "per-run" mode
    SETUP_MODE=per_run braintrust eval tests/llm/remote_evals --dev
"""
