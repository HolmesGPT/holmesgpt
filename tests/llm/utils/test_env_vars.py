import os

from holmes.common.env_vars import DEFAULT_MODEL

# Model configuration
MODEL = os.environ.get("MODEL", DEFAULT_MODEL)
CLASSIFIER_MODEL = os.environ.get(
    "CLASSIFIER_MODEL", os.environ.get("MODEL", DEFAULT_MODEL)
)
MODEL_LIST_FILE_LOCATION = os.environ.get("MODEL_LIST_FILE_LOCATION", "").strip()

# API Keys
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
AZURE_API_KEY = os.environ.get("AZURE_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BRAINTRUST_API_KEY = os.environ.get("BRAINTRUST_API_KEY")

# OpenAI configuration
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE")
OPENROUTER_API_BASE = os.environ.get(
    "OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"
)

# Azure configuration
AZURE_API_BASE = os.environ.get("AZURE_API_BASE")
AZURE_API_VERSION = os.environ.get("AZURE_API_VERSION")

# Test configuration
ASK_HOLMES_TEST_TYPE = os.environ.get("ASK_HOLMES_TEST_TYPE", "cli")

# Braintrust configuration
BRAINTRUST_ORG = os.environ.get("BRAINTRUST_ORG", "robustadev")
BRAINTRUST_PROJECT = os.environ.get("BRAINTRUST_PROJECT", "HolmesGPT")
EXPERIMENT_ID = os.environ.get("EXPERIMENT_ID")
GITHUB_REF_NAME = os.environ.get("GITHUB_REF_NAME")
BUILDKITE_BRANCH = os.environ.get("BUILDKITE_BRANCH")
