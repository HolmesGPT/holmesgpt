class LLMInterruptedError(Exception):
    """Raised when the user interrupts an in-progress LLM call (e.g. via Escape key)."""

    pass
