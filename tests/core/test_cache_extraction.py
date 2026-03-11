"""Test cache field extraction from LLM responses."""

from unittest.mock import MagicMock
from holmes.core.llm import get_llm_usage, TokenCountMetadata
from holmes.utils.stream import add_token_count_to_metadata


def test_bedrock_cache_fields():
    """Test that get_llm_usage extracts Bedrock cache fields correctly."""

    # Create mock response with Bedrock cache format
    mock_response = MagicMock()
    mock_usage = MagicMock()

    # Set up the usage object with Bedrock field names (snake_case)
    mock_usage.prompt_tokens = 480
    mock_usage.completion_tokens = 447
    mock_usage.total_tokens = 115557
    mock_usage.cache_read_input_tokens = 112761
    mock_usage.cache_creation_input_tokens = 1869

    mock_response.usage = mock_usage

    # Call get_llm_usage
    usage_dict = get_llm_usage(mock_response)

    # Verify basic tokens
    assert usage_dict["prompt_tokens"] == 480
    assert usage_dict["completion_tokens"] == 447
    assert usage_dict["total_tokens"] == 115557

    # Verify cache fields
    assert "cache_read_input_tokens" in usage_dict
    assert usage_dict["cache_read_input_tokens"] == 112761

    assert "cache_creation_input_tokens" in usage_dict
    assert usage_dict["cache_creation_input_tokens"] == 1869


def test_openai_cache_fields():
    """Test that get_llm_usage extracts OpenAI/Anthropic cache fields correctly."""

    # Create mock response with OpenAI/Anthropic cache format
    mock_response = MagicMock()
    mock_usage = MagicMock()
    mock_prompt_details = MagicMock()

    # Set up the usage object with OpenAI format
    mock_usage.prompt_tokens = 1000
    mock_usage.completion_tokens = 200
    mock_usage.total_tokens = 1200
    mock_usage.cache_read_input_tokens = None
    mock_usage.cache_creation_input_tokens = None

    # OpenAI uses prompt_tokens_details
    mock_prompt_details.cached_tokens = 800
    mock_prompt_details.cache_creation_input_tokens = 100
    mock_usage.prompt_tokens_details = mock_prompt_details

    mock_response.usage = mock_usage

    # Call get_llm_usage
    usage_dict = get_llm_usage(mock_response)

    # Verify cache details
    assert "prompt_tokens_details" in usage_dict
    details = usage_dict["prompt_tokens_details"]

    assert "cached_tokens" in details
    assert details["cached_tokens"] == 800

    assert "cache_creation_input_tokens" in details
    assert details["cache_creation_input_tokens"] == 100


def test_no_cache_fields():
    """Test that get_llm_usage works when no cache fields are present."""

    # Create mock response without cache fields
    mock_response = MagicMock()
    mock_usage = MagicMock()

    # Only basic fields
    mock_usage.prompt_tokens = 500
    mock_usage.completion_tokens = 100
    mock_usage.total_tokens = 600
    mock_usage.cache_read_input_tokens = None
    mock_usage.cache_creation_input_tokens = None
    mock_usage.prompt_tokens_details = None

    mock_response.usage = mock_usage

    # Call get_llm_usage
    usage_dict = get_llm_usage(mock_response)

    # Verify basic tokens
    assert usage_dict["prompt_tokens"] == 500
    assert usage_dict["completion_tokens"] == 100
    assert usage_dict["total_tokens"] == 600

    # No cache fields should be present
    assert "cache_read_input_tokens" not in usage_dict
    assert "cache_creation_input_tokens" not in usage_dict
    assert "prompt_tokens_details" not in usage_dict


def test_cache_fields_in_metadata():
    """Test that cache fields flow through to metadata (as returned by /api/chat)."""

    # Create mock response with Bedrock cache format
    mock_response = MagicMock()
    mock_usage = MagicMock()

    mock_usage.prompt_tokens = 480
    mock_usage.completion_tokens = 447
    mock_usage.total_tokens = 115557
    mock_usage.cache_read_input_tokens = 112761
    mock_usage.cache_creation_input_tokens = 1869

    mock_response.usage = mock_usage

    # Create mock token count metadata
    tokens = TokenCountMetadata(
        total_tokens=115557,
        tools_tokens=0,
        system_tokens=100,
        user_tokens=380,
        tools_to_call_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )

    # Create metadata dict and add token count (simulating API response flow)
    metadata = {}
    add_token_count_to_metadata(
        tokens=tokens,
        metadata=metadata,
        max_context_size=200000,
        maximum_output_token=16384,
        full_llm_response=mock_response,
    )

    # Verify cache fields are in metadata.usage (as returned by /api/chat)
    assert "usage" in metadata
    usage = metadata["usage"]

    assert usage["prompt_tokens"] == 480
    assert usage["completion_tokens"] == 447
    assert usage["total_tokens"] == 115557

    # Verify cache metrics are present
    assert "cache_read_input_tokens" in usage
    assert usage["cache_read_input_tokens"] == 112761

    assert "cache_creation_input_tokens" in usage
    assert usage["cache_creation_input_tokens"] == 1869
