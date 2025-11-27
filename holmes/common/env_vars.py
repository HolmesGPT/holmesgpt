import os
import json
from typing import Optional

# Recommended models for different providers
RECOMMENDED_OPENAI_MODEL = "gpt-4.1"
RECOMMENDED_ANTHROPIC_MODEL = "anthropic/claude-opus-4-1-20250805"

# Default model for HolmesGPT
DEFAULT_MODEL = RECOMMENDED_OPENAI_MODEL
FALLBACK_CONTEXT_WINDOW_SIZE = (
    200000  # Fallback context window size if it can't be determined from the model
)


def load_bool(env_var, default: Optional[bool]) -> Optional[bool]:
    env_value = os.environ.get(env_var)
    if env_value is None:
        return default

    return json.loads(env_value.lower())


ENABLED_BY_DEFAULT_TOOLSETS = os.environ.get(
    "ENABLED_BY_DEFAULT_TOOLSETS", "kubernetes/core,kubernetes/logs,robusta,internet"
)
HOLMES_HOST = os.environ.get("HOLMES_HOST", "0.0.0.0")
HOLMES_PORT = int(os.environ.get("HOLMES_PORT", 5050))
ROBUSTA_CONFIG_PATH = os.environ.get(
    "ROBUSTA_CONFIG_PATH", "/etc/robusta/config/active_playbooks.yaml"
)

ROBUSTA_ACCOUNT_ID = os.environ.get("ROBUSTA_ACCOUNT_ID", "")
STORE_URL = os.environ.get("STORE_URL", "")
STORE_API_KEY = os.environ.get("STORE_API_KEY", "")
STORE_EMAIL = os.environ.get("STORE_EMAIL", "")
STORE_PASSWORD = os.environ.get("STORE_PASSWORD", "")
HOLMES_POST_PROCESSING_PROMPT = os.environ.get("HOLMES_POST_PROCESSING_PROMPT", "")
ROBUSTA_AI = load_bool("ROBUSTA_AI", None)
LOAD_ALL_ROBUSTA_MODELS = load_bool("LOAD_ALL_ROBUSTA_MODELS", True)
ROBUSTA_API_ENDPOINT = os.environ.get("ROBUSTA_API_ENDPOINT", "https://api.robusta.dev")

LOG_PERFORMANCE = os.environ.get("LOG_PERFORMANCE", None)


ENABLE_TELEMETRY = load_bool("ENABLE_TELEMETRY", False)
DEVELOPMENT_MODE = load_bool("DEVELOPMENT_MODE", False)
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0"))

EXTRA_HEADERS = os.environ.get("EXTRA_HEADERS", "")
THINKING = os.environ.get("THINKING", "")
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "").strip().lower()
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.00000001"))

STREAM_CHUNKS_PER_PARSE = int(
    os.environ.get("STREAM_CHUNKS_PER_PARSE", 80)
)  # Empirical value with 6~ parsing calls. Consider using larger value if LLM response is long as to reduce markdown to section calls.

USE_LEGACY_KUBERNETES_LOGS = load_bool("USE_LEGACY_KUBERNETES_LOGS", False)
KUBERNETES_LOGS_TIMEOUT_SECONDS = int(
    os.environ.get("KUBERNETES_LOGS_TIMEOUT_SECONDS", 60)
)

TOOL_CALL_SAFEGUARDS_ENABLED = load_bool("TOOL_CALL_SAFEGUARDS_ENABLED", True)
IS_OPENSHIFT = load_bool("IS_OPENSHIFT", False)

LLMS_WITH_STRICT_TOOL_CALLS = os.environ.get(
    "LLMS_WITH_STRICT_TOOL_CALLS", "azure/gpt-4o, openai/*"
)
TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS = load_bool(
    "TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS", False
)

MAX_OUTPUT_TOKEN_RESERVATION = int(
    os.environ.get("MAX_OUTPUT_TOKEN_RESERVATION", 16384)
)  ## 16k

# When using the bash tool, setting BASH_TOOL_UNSAFE_ALLOW_ALL will skip any command validation and run any command requested by the LLM
BASH_TOOL_UNSAFE_ALLOW_ALL = load_bool("BASH_TOOL_UNSAFE_ALLOW_ALL", False)

LOG_LLM_USAGE_RESPONSE = load_bool("LOG_LLM_USAGE_RESPONSE", False)

# For CLI only, enable user approval for potentially sensitive commands that would otherwise be rejected
ENABLE_CLI_TOOL_APPROVAL = load_bool("ENABLE_CLI_TOOL_APPROVAL", True)

MAX_GRAPH_POINTS = float(os.environ.get("MAX_GRAPH_POINTS", 100))

# Limit each tool response to N% of the total context window.
# Number between 0 and 100
# Setting to either 0 or any number above 100 disables the logic that limits tool response size
TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT = float(
    os.environ.get("TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT", 15)
)

# Absolute max tokens to allocate for a single tool response
TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS = int(
    os.environ.get("TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS", 25000)
)

MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION = int(
    os.environ.get("MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION", 3000)
)

ENABLE_CONVERSATION_HISTORY_COMPACTION = load_bool(
    "ENABLE_CONVERSATION_HISTORY_COMPACTION", default=True
)

DISABLE_PROMETHEUS_TOOLSET = load_bool("DISABLE_PROMETHEUS_TOOLSET", False)

RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION = load_bool(
    "RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION", True
)

SSE_READ_TIMEOUT = float(os.environ.get("SSE_READ_TIMEOUT", "120"))
