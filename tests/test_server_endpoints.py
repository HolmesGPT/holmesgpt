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
def test_api_workload_health_chat(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    client,
):
    mock_ai = MagicMock()
    mock_ai.messages_call.return_value = MagicMock(
        result="This is a mock analysis for workload health chat.",
        tool_calls=[
            {
                "tool_call_id": "1",
                "tool_name": "health_checker",
                "description": "Checks workload health",
                "result": {"status": "success", "data": "Workload is healthy"},
            }
        ],
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Check the workload health."},
        ],
        metadata={},
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    mock_get_global_instructions.return_value = []

    payload = {
        "ask": "Check the workload health.",
        "workload_health_result": {
            "analysis": "Mock workload health analysis",
            "tools": [],
        },
        "resource": {"name": "example-resource", "kind": "Deployment"},
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Check the workload health."},
        ],
    }
    response = client.post("/api/workload_health_chat", json=payload)
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
