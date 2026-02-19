"""
Mocked tests for /api/chat endpoint.

These tests mock only litellm.completion (and necessary dependencies) to:
1. Test intermediate streaming events (tool calls, reasoning)
2. Test error handling behavior
3. Provide fast, deterministic tests for confident refactoring

The mocked tests verify our code's behavior without being tied to
implementation details - if we refactor internals but keep the same
public HTTP API behavior, these tests should still pass.
"""

import json
from types import SimpleNamespace
from typing import List, Optional, Tuple
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from holmes.utils.stream import StreamEvents

# Model name for mocked tests
MOCK_MODEL = "gpt-4.1"


def parse_sse_events(response_text: str) -> List[Tuple[str, dict]]:
    """
    Parse SSE events from response text.

    Returns a list of (event_type, data) tuples.
    """
    events = []
    current_event = None
    for line in response_text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: ") and current_event:
            try:
                data = json.loads(line[6:])
                events.append((current_event, data))
            except json.JSONDecodeError:
                pass
    return events


def create_model_entry(model_name: str):
    """Create a ModelEntry for the given model."""
    from holmes.core.llm import ModelEntry

    return ModelEntry(
        name=model_name,
        model=model_name,
        api_key=None,
    )


@pytest.fixture
def mock_client():
    """
    Create a test client for mocked LLM tests.
    Does not require any API keys - litellm.completion is mocked.
    """
    import server

    # Set up a mock model entry so the server doesn't try to use ROBUSTA_AI
    model_entry = create_model_entry(MOCK_MODEL)
    original_get_model_params = server.config.llm_model_registry.get_model_params

    def mock_get_model_params(model_key=None):
        if model_key == MOCK_MODEL:
            return model_entry.model_copy()
        return original_get_model_params(model_key)

    server.config.llm_model_registry.get_model_params = mock_get_model_params

    try:
        yield TestClient(server.app)
    finally:
        server.config.llm_model_registry.get_model_params = original_get_model_params


def create_mock_llm_response(
    content: str, tool_calls: Optional[list] = None, reasoning: Optional[str] = None
):
    """
    Create a proper litellm ModelResponse object.

    This creates an actual ModelResponse (not a mock) that will pass
    type validation in our code.
    """
    from litellm import ModelResponse
    from litellm.types.utils import Choices, Message, Usage

    # Build tool_calls for the message if provided
    message_tool_calls = None
    if tool_calls:
        message_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]

    # Create the message
    message_dict = {
        "role": "assistant",
        "content": content,
    }
    if message_tool_calls:
        message_dict["tool_calls"] = message_tool_calls
    if reasoning:
        message_dict["reasoning_content"] = reasoning

    message = Message(**message_dict)

    # Create the response
    response = ModelResponse(
        id="chatcmpl-test-123",
        choices=[
            Choices(
                finish_reason="stop" if not tool_calls else "tool_calls",
                index=0,
                message=message,
            )
        ],
        created=1234567890,
        model="gpt-4.1-mini",
        usage=Usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
    )

    return response


def create_mock_tool_call(tool_call_id: str, tool_name: str, arguments: dict):
    """Create a tool call object compatible with litellm."""
    function = SimpleNamespace(
        name=tool_name,
        arguments=json.dumps(arguments),
    )
    tool_call = SimpleNamespace(
        id=tool_call_id,
        type="function",
        function=function,
    )
    return tool_call


# =============================================================================
# INTERMEDIATE EVENT TESTS - Verify streaming emits correct events
# =============================================================================


class TestStreamingIntermediateEvents:
    """
    Tests for intermediate streaming events (tool calls, AI messages, etc.)

    These tests mock only litellm.completion to verify our code correctly
    emits intermediate events. This allows confident refactoring without
    being tied to implementation details.
    """

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_emits_tool_events(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that streaming correctly emits START_TOOL and TOOL_RESULT events.

        Mocks litellm.completion to return a tool call, then verifies our
        streaming code emits the correct intermediate events.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        # First call: LLM wants to call a tool
        tool_call = create_mock_tool_call(
            tool_call_id="call_123",
            tool_name="fetch_url",
            arguments={"url": "https://example.com"},
        )
        response_with_tool = create_mock_llm_response(
            content="Let me fetch that URL for you.",
            tool_calls=[tool_call],
        )

        # Second call: LLM provides final answer after tool result
        final_response = create_mock_llm_response(
            content="The URL returned a 200 OK response.",
            tool_calls=None,
        )

        mock_litellm_completion.side_effect = [response_with_tool, final_response]

        payload = {
            "ask": "Check if example.com is up",
            "conversation_history": [
                {"role": "system", "content": "You are a helpful assistant."}
            ],
            "stream": True,
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        events = parse_sse_events(response.text)
        event_types = [e[0] for e in events]

        # Verify tool events are present
        assert (
            StreamEvents.START_TOOL.value in event_types
        ), f"Missing START_TOOL, got: {event_types}"
        assert (
            StreamEvents.TOOL_RESULT.value in event_types
        ), f"Missing TOOL_RESULT, got: {event_types}"
        assert (
            StreamEvents.ANSWER_END.value in event_types
        ), f"Missing ANSWER_END, got: {event_types}"

        # Verify START_TOOL has correct data
        start_tool_event = next(
            (e[1] for e in events if e[0] == StreamEvents.START_TOOL.value), None
        )
        assert start_tool_event is not None
        assert start_tool_event["tool_name"] == "fetch_url"
        assert start_tool_event["id"] == "call_123"

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_completes_with_expected_content(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that streaming completes successfully and ANSWER_END contains expected content.

        Verifies that when the LLM returns a response (with or without reasoning),
        the streaming flow completes and the final answer is included in ANSWER_END.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        # Response with reasoning content
        response_with_reasoning = create_mock_llm_response(
            content="The answer is 42.",
            tool_calls=None,
            reasoning="I need to think about the meaning of life...",
        )

        mock_litellm_completion.return_value = response_with_reasoning

        payload = {
            "ask": "What is the meaning of life?",
            "conversation_history": [{"role": "system", "content": "Think deeply."}],
            "stream": True,
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        events = parse_sse_events(response.text)
        event_types = [e[0] for e in events]

        # Should complete with ANSWER_END
        assert (
            StreamEvents.ANSWER_END.value in event_types
        ), f"Missing ANSWER_END, got: {event_types}"

        # Find ANSWER_END event
        answer_end = next(
            (e[1] for e in events if e[0] == StreamEvents.ANSWER_END.value), None
        )
        assert answer_end is not None

        # Verify the final answer contains our expected content
        assert "analysis" in answer_end
        assert (
            "42" in answer_end["analysis"]
        ), f"Expected '42' in analysis, got: {answer_end['analysis']}"

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_answer_end_contains_full_response(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that ANSWER_END event contains the complete response and metadata.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        final_answer = "Here is my complete analysis of your question."
        mock_litellm_completion.return_value = create_mock_llm_response(
            content=final_answer,
            tool_calls=None,
        )

        payload = {
            "ask": "Analyze something",
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "stream": True,
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        events = parse_sse_events(response.text)

        # Find ANSWER_END event
        answer_end = next(
            (e[1] for e in events if e[0] == StreamEvents.ANSWER_END.value), None
        )
        assert answer_end is not None, "No ANSWER_END event found"

        # Verify it contains the analysis
        assert "analysis" in answer_end
        assert final_answer in answer_end["analysis"]

        # Verify it contains conversation history
        assert "conversation_history" in answer_end

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_non_streaming_returns_tool_calls_in_response(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that non-streaming responses include tool_calls in the response.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        # LLM calls a tool then gives final answer
        tool_call = create_mock_tool_call(
            tool_call_id="call_456",
            tool_name="get_weather",
            arguments={"location": "NYC"},
        )
        response_with_tool = create_mock_llm_response(
            content="Let me check the weather.",
            tool_calls=[tool_call],
        )
        final_response = create_mock_llm_response(
            content="The weather in NYC is sunny.",
            tool_calls=None,
        )
        mock_litellm_completion.side_effect = [response_with_tool, final_response]

        payload = {
            "ask": "What's the weather in NYC?",
            "conversation_history": [
                {"role": "system", "content": "Help with weather."}
            ],
            "stream": False,
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        data = response.json()

        # Verify response structure
        assert "analysis" in data
        assert "tool_calls" in data
        assert "conversation_history" in data

        # Should have recorded the tool call
        assert len(data["tool_calls"]) > 0


# =============================================================================
# ERROR HANDLING TESTS - Verify correct HTTP status codes
# =============================================================================


class TestErrorHandling:
    """
    Tests for error handling in /api/chat endpoint.

    These tests verify that the API returns correct HTTP status codes
    for various error conditions.
    """

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_rate_limit_returns_429(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that rate limit errors return HTTP 429.
        """
        import litellm.exceptions

        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []
        mock_litellm_completion.side_effect = litellm.exceptions.RateLimitError(
            message="Rate limit exceeded",
            llm_provider="openai",
            model="gpt-4.1",
            response=None,
        )

        payload = {
            "ask": "Hello",
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 429

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_authentication_error_returns_401(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that authentication errors return HTTP 401.
        """
        from litellm.exceptions import AuthenticationError

        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []
        mock_litellm_completion.side_effect = AuthenticationError(
            message="Invalid API key",
            llm_provider="openai",
            model="gpt-4.1",
        )

        payload = {
            "ask": "Hello",
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 401

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_generic_error_returns_500(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that generic errors return HTTP 500.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []
        mock_litellm_completion.side_effect = Exception("Something went wrong")

        payload = {
            "ask": "Hello",
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 500

    def test_invalid_conversation_history_returns_422(self, mock_client):
        """
        Test that invalid conversation_history format returns HTTP 422.

        The first message must have role: system.
        """
        payload = {
            "ask": "Hello",
            "conversation_history": [
                {"role": "user", "content": "Not a system message"}  # Invalid!
            ],
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 422  # Validation error

    def test_missing_ask_returns_422(self, mock_client):
        """
        Test that missing 'ask' field returns HTTP 422.
        """
        payload = {
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "model": MOCK_MODEL,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 422  # Validation error


# =============================================================================
# RESPONSE STRUCTURE TESTS - Verify response format
# =============================================================================


class TestResponseStructure:
    """
    Tests for verifying response structure and fields.
    """

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_response_includes_follow_up_actions(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that response includes follow_up_actions on first message.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []
        mock_litellm_completion.return_value = create_mock_llm_response(
            content="Here is my answer.",
            tool_calls=None,
        )

        payload = {
            "ask": "What's wrong with my pod?",
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "model": MOCK_MODEL,
            "stream": False,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        data = response.json()

        # Verify follow_up_actions are present
        assert "follow_up_actions" in data
        actions = data["follow_up_actions"]
        assert isinstance(actions, list)
        assert len(actions) > 0

        # Verify action structure
        action_ids = [a["id"] for a in actions]
        assert "logs" in action_ids
        assert "graphs" in action_ids
        assert "articles" in action_ids

    @patch("holmes.core.llm.DefaultLLM.check_llm")
    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_follow_up_actions_in_answer_end(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_check_llm,
        mock_client,
    ):
        """
        Test that streaming ANSWER_END event includes follow_up_actions.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []
        mock_litellm_completion.return_value = create_mock_llm_response(
            content="Here is my answer.",
            tool_calls=None,
        )

        payload = {
            "ask": "What's wrong with my pod?",
            "conversation_history": [{"role": "system", "content": "Be helpful."}],
            "model": MOCK_MODEL,
            "stream": True,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        events = parse_sse_events(response.text)
        answer_end = next(
            (e[1] for e in events if e[0] == StreamEvents.ANSWER_END.value), None
        )

        assert answer_end is not None
        assert "follow_up_actions" in answer_end
        actions = answer_end["follow_up_actions"]
        assert len(actions) > 0
