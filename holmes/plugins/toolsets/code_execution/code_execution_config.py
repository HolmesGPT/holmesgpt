from holmes.utils.pydantic_utils import ToolsetConfig


class CodeExecutionConfig(ToolsetConfig):
    # Default wall-clock timeout for a script, in seconds. The LLM may pass a
    # smaller/larger value per call; it is clamped to max_timeout_seconds.
    default_timeout_seconds: int = 60
    max_timeout_seconds: int = 300
