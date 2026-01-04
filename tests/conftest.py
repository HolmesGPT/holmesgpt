import logging
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

from holmes.config import Config
from holmes.core.llm import LLM, TokenCountMetadata
from holmes.core.tools import ToolInvokeContext

DEFAULT_ROBUSTA_MODEL = "Robusta/gpt-5-mini preview (minimal reasoning)"
ROBUSTA_SONNET_4_MODEL = "Robusta/sonnet-4 preview"

# Map of Robusta model names to their underlying LiteLLM model names
ROBUSTA_MODELS: dict[str, Any] = {
    ROBUSTA_SONNET_4_MODEL: {
        "model": "claude-sonnet-4-20250514",
        "holmes_args": {},
        "is_default": False,
    },
    DEFAULT_ROBUSTA_MODEL: {
        "model": "azure/gpt-5-mini",
        "holmes_args": {},
        "is_default": True,
    },
    "Robusta/gpt-5 preview (minimal reasoning)": {
        "model": "azure/gpt-5",
        "holmes_args": {},
        "is_default": False,
    },
    "Robusta/gpt-4o": {
        "model": "azure/gpt-4o",
        "holmes_args": {"api_version": "XYZ"},
        "is_default": False,
    },
}


@pytest.fixture(autouse=True, scope="session")
def setup_logging():
    """Setup logging for the test session."""
    logging.getLogger("responses").setLevel(logging.WARNING)


@pytest.fixture(autouse=True, scope="function")
def clear_all_caches():
    """Clear all function caches that may affect test isolation."""
    try:
        import holmes.clients.robusta_client as rc

        rc.fetch_robusta_models.cache_clear()
        rc.fetch_holmes_info.cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=False)
def server_config(tmp_path, monkeypatch, responses):
    responses.post(
        "https://api.robusta.dev/api/llm/models/v2",
        json=ROBUSTA_MODELS,
    )
    temp_config_file = tmp_path / "custom_toolset.yaml"
    data = {
        "our_local_model": {
            "model": "bedrock/custom_ai_model",
            "api_key": "existing_api_key",
        },
    }

    temp_config_file.write_text(yaml.dump(data))
    monkeypatch.setattr(
        "holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(temp_config_file)
    )
    monkeypatch.setattr("holmes.core.llm.ROBUSTA_AI", True)
    monkeypatch.setenv("CLUSTER_NAME", "test-cluster")

    return Config.load_from_env()


@pytest.fixture(autouse=False)
def cli_config():
    return get_cli_config()


def get_cli_config(config_file: Optional[Path] = None, **kwargs):
    return Config.load_from_file(config_file, **kwargs)


class MockLLM(LLM):
    """Mock LLM implementation for testing purposes."""

    def __init__(self, model: str = "mock-model"):
        self.model = model

    def get_context_window_size(self) -> int:
        return 8192

    def get_maximum_output_token(self) -> int:
        return 2048

    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> TokenCountMetadata:
        # Simple approximation: count characters and divide by 4
        total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
        return TokenCountMetadata(
            total_tokens=total_chars // 4,
            system_tokens=0,
            tools_to_call_tokens=0,
            tools_tokens=0,
            user_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )

    def completion(self, *args, **kwargs):  # type: ignore
        # Mock completion that returns a basic response
        mock_response = type(
            "MockResponse",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": type(
                                "Message", (), {"content": "Mock response"}
                            )()
                        },
                    )()
                ]
            },
        )()
        return mock_response


def create_mock_tool_invoke_context(
    tool_number: Optional[int] = None,
    user_approved: bool = False,
    max_token_count: int = 128000,
    llm: Optional[LLM] = None,
    tool_call_id: str = "test_call_123",
    tool_name: str = "test_tool",
) -> ToolInvokeContext:
    """
    Create a mock ToolInvokeContext for testing purposes.

    Args:
        tool_number: Optional tool number
        user_approved: Whether the tool is user approved
        max_token_count: Optional maximum token count
        llm: Optional LLM instance. If None, uses MockLLM
        tool_call_id: Tool call ID for testing
        tool_name: Tool name for testing

    Returns:
        ToolInvokeContext instance suitable for testing
    """
    if llm is None:
        llm = MockLLM()

    return ToolInvokeContext(
        tool_number=tool_number,
        user_approved=user_approved,
        llm=llm,
        max_token_count=max_token_count,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
