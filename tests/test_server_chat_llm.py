"""
Real LLM tests for /api/chat endpoint.

These tests use actual LLM API calls to verify end-to-end behavior.
They require API keys (OPENAI_API_KEY, OPENROUTER_API_KEY) and are
skipped if the keys are not available.

Tests cover:
1. Structured output with OpenAI strict JSON schema
2. Image extraction with vision models
3. Streaming responses
4. Conversation history roundtrip
"""

import base64
import json
import os
from io import BytesIO
from typing import List, Tuple

import pytest
from fastapi.testclient import TestClient

from holmes.utils.stream import StreamEvents

# Models for different test scenarios
OPENAI_MODEL = "gpt-4.1-mini"  # For structured output tests (supports strict JSON schema)
OPENROUTER_MODEL = "openrouter/anthropic/claude-haiku-4.5"  # For image tests


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


def create_test_image_with_text(text: str, width: int = 200, height: int = 100) -> str:
    """
    Create a simple PNG image with text and return as base64 data URI.

    Uses PIL to create an image with the specified text rendered on it.
    This allows us to verify the LLM can actually read the image content.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pytest.skip("PIL/Pillow not installed - required for image tests")

    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.text((x, y), text, fill="black", font=font)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    b64_string = base64.b64encode(img_bytes).decode("utf-8")

    return f"data:image/png;base64,{b64_string}"


def create_model_entry(model_name: str):
    """Create a ModelEntry for the given model."""
    from holmes.core.llm import ModelEntry

    return ModelEntry(
        name=model_name,
        model=model_name,
        api_key=None,
    )


@pytest.fixture
def openai_client():
    """
    Create a test client configured for OpenAI model.
    Requires OPENAI_API_KEY environment variable.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    import server

    model_entry = create_model_entry(OPENAI_MODEL)
    original_get_model_params = server.config.llm_model_registry.get_model_params

    def mock_get_model_params(model_key=None):
        if model_key == OPENAI_MODEL:
            return model_entry.model_copy()
        return original_get_model_params(model_key)

    server.config.llm_model_registry.get_model_params = mock_get_model_params

    try:
        yield TestClient(server.app)
    finally:
        server.config.llm_model_registry.get_model_params = original_get_model_params


@pytest.fixture
def openrouter_client():
    """
    Create a test client configured for OpenRouter model (Claude Haiku 4.5).
    Requires OPENROUTER_API_KEY environment variable.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    import server

    model_entry = create_model_entry(OPENROUTER_MODEL)
    original_get_model_params = server.config.llm_model_registry.get_model_params

    def mock_get_model_params(model_key=None):
        if model_key == OPENROUTER_MODEL:
            return model_entry.model_copy()
        return original_get_model_params(model_key)

    server.config.llm_model_registry.get_model_params = mock_get_model_params

    try:
        yield TestClient(server.app)
    finally:
        server.config.llm_model_registry.get_model_params = original_get_model_params


# =============================================================================
# STRUCTURED OUTPUT TESTS - Real LLM with OpenAI strict JSON schema
# =============================================================================


@pytest.mark.integration
class TestStructuredOutputReal:
    """
    Tests for OpenAI structured output with strict JSON schema.

    These tests verify that the response_format parameter correctly
    constrains the LLM output to match the specified JSON schema.
    """

    def test_strict_json_schema_basic(self, openai_client):
        """
        Test strict JSON schema with a simple schema.

        OpenAI's strict mode guarantees the response matches the schema exactly.
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "SentimentResult",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "confidence": {
                            "type": "number",
                        },
                    },
                    "required": ["sentiment", "confidence"],
                    "additionalProperties": False,
                },
            },
        }

        payload = {
            "ask": "Analyze: 'I love this product!' - return sentiment and confidence.",
            "conversation_history": [
                {"role": "system", "content": "Analyze sentiment and return JSON."}
            ],
            "model": OPENAI_MODEL,
            "response_format": response_format,
        }

        response = openai_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        assert "analysis" in data

        # With strict mode, response MUST be valid JSON matching schema
        analysis = json.loads(data["analysis"])
        assert analysis["sentiment"] in ["positive", "negative", "neutral"]
        assert isinstance(analysis["confidence"], (int, float))
        # Should detect positive sentiment
        assert analysis["sentiment"] == "positive"

    def test_strict_json_schema_nested(self, openai_client):
        """
        Test strict JSON schema with nested objects.
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "TaskAnalysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                        "metadata": {
                            "type": "object",
                            "properties": {
                                "estimated_hours": {"type": "integer"},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["estimated_hours", "tags"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["summary", "priority", "metadata"],
                    "additionalProperties": False,
                },
            },
        }

        payload = {
            "ask": "Analyze task: 'Set up CI/CD pipeline' - return analysis.",
            "conversation_history": [
                {"role": "system", "content": "Analyze tasks and return structured JSON."}
            ],
            "model": OPENAI_MODEL,
            "response_format": response_format,
        }

        response = openai_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        analysis = json.loads(data["analysis"])

        # Verify nested structure
        assert "summary" in analysis
        assert analysis["priority"] in ["low", "medium", "high"]
        assert "metadata" in analysis
        assert isinstance(analysis["metadata"]["estimated_hours"], int)
        assert isinstance(analysis["metadata"]["tags"], list)


# =============================================================================
# IMAGE EXTRACTION TESTS - Real LLM with vision capabilities
# =============================================================================


@pytest.mark.integration
class TestImageExtraction:
    """
    Tests for image content extraction using real LLM vision capabilities.

    These tests create images with known content and verify the LLM
    correctly extracts information that it couldn't know in advance.
    """

    def test_image_text_extraction(self, openrouter_client):
        """
        Test that the LLM can extract text from an image.
        """
        secret_code = "HOLMES-7X9K2M"
        image_data_uri = create_test_image_with_text(secret_code)

        payload = {
            "ask": "What text do you see in this image? Reply with ONLY the exact text.",
            "conversation_history": [
                {"role": "system", "content": "Read text from images. Reply with only the text."}
            ],
            "model": OPENROUTER_MODEL,
            "images": [image_data_uri],
        }

        response = openrouter_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        analysis = data["analysis"].strip()

        # Allow for minor formatting differences
        assert secret_code in analysis or secret_code.replace("-", "") in analysis.replace(
            "-", ""
        ), f"Expected '{secret_code}' in response, got: '{analysis}'"

    def test_image_number_extraction(self, openrouter_client):
        """
        Test image extraction with numerical content.
        """
        test_number = "42857"
        image_data_uri = create_test_image_with_text(test_number)

        payload = {
            "ask": "What number is shown? Reply with only the number.",
            "conversation_history": [
                {"role": "system", "content": "Read numbers from images."}
            ],
            "model": OPENROUTER_MODEL,
            "images": [image_data_uri],
        }

        response = openrouter_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        extracted = "".join(c for c in data["analysis"] if c.isdigit())
        assert test_number in extracted, f"Expected '{test_number}', got: '{data['analysis']}'"


# =============================================================================
# STREAMING TESTS - Real LLM streaming responses
# =============================================================================


@pytest.mark.integration
class TestStreamingReal:
    """Tests for streaming responses with real LLM."""

    def test_streaming_basic_events(self, openrouter_client):
        """
        Test that streaming returns proper SSE events.
        """
        payload = {
            "ask": "Say hello in exactly 3 words.",
            "conversation_history": [{"role": "system", "content": "Be concise."}],
            "model": OPENROUTER_MODEL,
            "stream": True,
        }

        with openrouter_client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200

            events = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line[7:].strip())

            assert len(events) > 0, "No SSE events received"
            assert StreamEvents.ANSWER_END.value in events


# =============================================================================
# CONVERSATION ROUNDTRIP TESTS - Verify state management across calls
# =============================================================================


@pytest.mark.integration
class TestConversationRoundtrip:
    """
    Tests for conversation history management.

    These tests verify that the conversation_history returned in responses
    can be used in subsequent requests to maintain conversation context.
    """

    def test_conversation_history_roundtrip(self, openai_client):
        """
        Test that conversation_history from response can be used in next request.

        This verifies the state management works correctly across multiple calls.
        """
        # First request - ask a question with a unique answer
        secret_value = "XYZZY-12345"
        payload1 = {
            "ask": f"Remember this secret code: {secret_value}. Just say 'OK, I remember.'",
            "conversation_history": [
                {"role": "system", "content": "You are a helpful assistant with perfect memory."}
            ],
            "model": OPENAI_MODEL,
        }

        response1 = openai_client.post("/api/chat", json=payload1)
        assert response1.status_code == 200, f"First request failed: {response1.text}"

        data1 = response1.json()
        assert "conversation_history" in data1
        conversation_history = data1["conversation_history"]

        # Second request - use the returned conversation_history
        payload2 = {
            "ask": "What was the secret code I told you to remember?",
            "conversation_history": conversation_history,
            "model": OPENAI_MODEL,
        }

        response2 = openai_client.post("/api/chat", json=payload2)
        assert response2.status_code == 200, f"Second request failed: {response2.text}"

        data2 = response2.json()
        # The LLM should remember the secret code from the conversation history
        assert secret_value in data2["analysis"], (
            f"Expected '{secret_value}' in response, got: '{data2['analysis']}'"
        )

    def test_conversation_history_accumulates(self, openai_client):
        """
        Test that conversation history grows with each exchange.
        """
        # First request
        payload1 = {
            "ask": "Say 'one'",
            "conversation_history": [
                {"role": "system", "content": "Reply with exactly what is asked, nothing more."}
            ],
            "model": OPENAI_MODEL,
        }

        response1 = openai_client.post("/api/chat", json=payload1)
        assert response1.status_code == 200
        data1 = response1.json()
        history1 = data1["conversation_history"]

        # History should have: system + user + assistant = at least 3 messages
        assert len(history1) >= 3, f"Expected at least 3 messages, got {len(history1)}"

        # Second request with updated history
        payload2 = {
            "ask": "Say 'two'",
            "conversation_history": history1,
            "model": OPENAI_MODEL,
        }

        response2 = openai_client.post("/api/chat", json=payload2)
        assert response2.status_code == 200
        data2 = response2.json()
        history2 = data2["conversation_history"]

        # History should have grown by 2 (user + assistant)
        assert len(history2) >= len(history1) + 2, (
            f"Expected history to grow from {len(history1)} to at least {len(history1) + 2}, "
            f"got {len(history2)}"
        )
