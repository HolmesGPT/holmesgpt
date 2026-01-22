from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client():
    return TestClient(app)


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_chat_all_fields(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    mock_ai = MagicMock()
    mock_ai.messages_call.return_value = MagicMock(
        result="This is a mock analysis with tools and follow-up actions.",
        tool_calls=[
            {
                "tool_call_id": "1",
                "tool_name": "log_fetcher",
                "description": "Fetches logs",
                "result": {"status": "success", "data": "Log data"},
            }
        ],
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What can you do?"},
        ],
        metadata={},
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    mock_get_global_instructions.return_value = []

    payload = {
        "ask": "What can you do?",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        "model": "gpt-4.1",
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "analysis" in data
    assert "conversation_history" in data
    assert "tool_calls" in data
    assert "follow_up_actions" in data

    assert isinstance(data["analysis"], str)
    assert isinstance(data["conversation_history"], list)
    assert isinstance(data["tool_calls"], list)
    assert isinstance(data["follow_up_actions"], list)

    assert any(msg.get("role") == "user" for msg in data["conversation_history"])

    if data["tool_calls"]:
        tool_call = data["tool_calls"][0]
        assert "tool_call_id" in tool_call
        assert "tool_name" in tool_call
        assert "description" in tool_call
        assert "result" in tool_call

    if data["follow_up_actions"]:
        action = data["follow_up_actions"][0]
        assert "id" in action
        assert "action_label" in action
        assert "prompt" in action
        assert "pre_action_notification_text" in action


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_chat_with_images(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    """Test /api/chat endpoint with image analysis support."""
    mock_ai = MagicMock()

    # Capture the messages passed to the LLM
    captured_messages = []

    def capture_messages(messages, **kwargs):
        captured_messages.append(messages)
        return MagicMock(
            result="This is an analysis of the provided image.",
            tool_calls=[],
            messages=messages,
            metadata={},
        )

    mock_ai.messages_call.side_effect = capture_messages
    mock_create_toolcalling_llm.return_value = mock_ai
    mock_get_global_instructions.return_value = []

    payload = {
        "ask": "What's in this image?",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        "model": "gpt-4-vision-preview",
        "images": [
            "https://example.com/image1.png",
            "https://example.com/image2.jpg",
        ],
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "analysis" in data
    assert "conversation_history" in data
    assert "tool_calls" in data
    assert "follow_up_actions" in data

    # Verify the messages were captured
    assert len(captured_messages) == 1
    messages = captured_messages[0]

    # Find the user message with images
    user_message = next((m for m in messages if m["role"] == "user"), None)
    assert user_message is not None

    # Verify the content is an array with text and images
    content = user_message["content"]
    assert isinstance(content, list)
    assert len(content) == 3  # 1 text + 2 images

    # Verify text content
    text_item = content[0]
    assert text_item["type"] == "text"
    assert "What's in this image?" in text_item["text"]

    # Verify image contents
    image_items = content[1:]
    assert len(image_items) == 2
    for i, image_item in enumerate(image_items):
        assert image_item["type"] == "image_url"
        assert "image_url" in image_item
        assert image_item["image_url"]["url"] == payload["images"][i]


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_chat_with_images_advanced_format(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    """Test /api/chat endpoint with advanced image format (dict with detail and format)."""
    mock_ai = MagicMock()

    # Capture the messages passed to the LLM
    captured_messages = []

    def capture_messages(messages, **kwargs):
        captured_messages.append(messages)
        return MagicMock(
            result="Detailed analysis of high-resolution image.",
            tool_calls=[],
            messages=messages,
            metadata={},
        )

    mock_ai.messages_call.side_effect = capture_messages
    mock_create_toolcalling_llm.return_value = mock_ai
    mock_get_global_instructions.return_value = []

    payload = {
        "ask": "Analyze this screenshot in detail",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        "model": "gpt-4o",
        "images": [
            # Mix of simple strings and advanced dict format
            "https://example.com/simple-url.png",
            {
                "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                "detail": "high",
            },
            {
                "url": "https://example.com/image-with-format.webp",
                "detail": "low",
                "format": "image/webp",
            },
        ],
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "analysis" in data
    assert "conversation_history" in data

    # Verify the messages were captured
    assert len(captured_messages) == 1
    messages = captured_messages[0]

    # Find the user message with images
    user_message = next((m for m in messages if m["role"] == "user"), None)
    assert user_message is not None

    # Verify the content is an array
    content = user_message["content"]
    assert isinstance(content, list)
    assert len(content) == 4  # 1 text + 3 images

    # Verify text content
    text_item = content[0]
    assert text_item["type"] == "text"
    assert "Analyze this screenshot in detail" in text_item["text"]

    # Verify first image (simple string URL)
    image1 = content[1]
    assert image1["type"] == "image_url"
    assert image1["image_url"]["url"] == "https://example.com/simple-url.png"
    assert "detail" not in image1["image_url"]
    assert "format" not in image1["image_url"]

    # Verify second image (base64 with detail)
    image2 = content[2]
    assert image2["type"] == "image_url"
    assert image2["image_url"]["url"] == "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
    assert image2["image_url"]["detail"] == "high"
    assert "format" not in image2["image_url"]

    # Verify third image (URL with detail and format)
    image3 = content[3]
    assert image3["type"] == "image_url"
    assert image3["image_url"]["url"] == "https://example.com/image-with-format.webp"
    assert image3["image_url"]["detail"] == "low"
    assert image3["image_url"]["format"] == "image/webp"


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_chat_with_images_missing_url_key(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    """Test /api/chat endpoint raises error when image dict missing 'url' key."""
    mock_ai = MagicMock()
    mock_ai.messages_call.return_value = MagicMock(
        result="This should not be reached.",
        tool_calls=[],
        messages=[],
        metadata={},
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    mock_get_global_instructions.return_value = []

    payload = {
        "ask": "Analyze this",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        "model": "gpt-4o",
        "images": [
            # Dict missing required "url" key
            {"detail": "high", "format": "image/jpeg"}
        ],
    }
    response = client.post("/api/chat", json=payload)

    # Should return 500 error with clear message
    assert response.status_code == 500
    data = response.json()
    assert "Image dict must contain a 'url' key" in data["detail"]


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_api_issue_chat_all_fields(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    mock_ai = MagicMock()
    mock_ai.messages_call.return_value = MagicMock(
        result="This is a mock analysis for issue chat.",
        tool_calls=[
            {
                "tool_call_id": "1",
                "tool_name": "issue_resolver",
                "description": "Resolves issues",
                "result": {"status": "success", "data": "Issue resolved"},
            }
        ],
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "I have an issue with my deployment."},
        ],
        metadata={},
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    mock_get_global_instructions.return_value = []

    payload = {
        "ask": "What can you do?",
        "investigation_result": {"result": "Mock investigation result", "tools": []},
        "issue_type": "deployment",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "I have an issue with my deployment."},
        ],
    }
    response = client.post("/api/issue_chat", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "analysis" in data
    assert "conversation_history" in data
    assert "tool_calls" in data

    assert isinstance(data["analysis"], str)
    assert isinstance(data["conversation_history"], list)
    assert isinstance(data["tool_calls"], list)

    assert any(msg.get("role") == "user" for msg in data["conversation_history"])

    if data["tool_calls"]:
        tool_call = data["tool_calls"][0]
        assert "tool_call_id" in tool_call
        assert "tool_name" in tool_call
        assert "description" in tool_call
        assert "result" in tool_call


@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
@patch("holmes.core.supabase_dal.SupabaseDal.get_workload_issues")
@patch("holmes.core.supabase_dal.SupabaseDal.get_resource_instructions")
@patch("holmes.plugins.prompts.load_and_render_prompt")
def test_api_workload_health_check(
    mock_load_and_render_prompt,
    mock_get_resource_instructions,
    mock_get_workload_issues,
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    mock_ai = MagicMock()
    mock_ai.prompt_call.return_value = MagicMock(
        result="This is a mock analysis for workload health check.",
        tool_calls=[
            {
                "tool_call_id": "1",
                "tool_name": "health_checker",
                "description": "Checks workload health",
                "result": {"status": "success", "data": "Workload is healthy"},
            }
        ],
        metadata={},
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    mock_get_global_instructions.return_value = []
    mock_get_workload_issues.return_value = ["Alert 1", "Alert 2"]
    mock_get_resource_instructions.return_value = MagicMock(
        instructions=["Instruction 1", "Instruction 2"]
    )

    mock_load_and_render_prompt.return_value = "Mocked system prompt"

    payload = {
        "resource": {"name": "example-resource", "kind": "Deployment"},
        "alert_history": True,
        "alert_history_since_hours": 24,
        "instructions": ["Check CPU usage", "Check memory usage"],
        "stored_instructions": True,
        "ask": "Check the workload health.",
        "model": "gpt-4.1",
    }
    response = client.post("/api/workload_health_check", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "analysis" in data
    assert "tool_calls" in data
    assert "instructions" in data

    assert isinstance(data["analysis"], str)
    assert isinstance(data["tool_calls"], list)
    assert isinstance(data["instructions"], list)

    if data["tool_calls"]:
        tool_call = data["tool_calls"][0]
        assert "tool_call_id" in tool_call
        assert "tool_name" in tool_call
        assert "description" in tool_call
        assert "result" in tool_call
