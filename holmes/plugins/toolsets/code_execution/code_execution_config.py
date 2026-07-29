from pydantic import BaseModel, ConfigDict


class CodeExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Default wall-clock timeout for a script, in seconds. The LLM may pass a
    # smaller/larger value per call; it is clamped to max_timeout_seconds.
    default_timeout_seconds: int = 60
    max_timeout_seconds: int = 300
