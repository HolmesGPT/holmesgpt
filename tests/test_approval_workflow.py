import json
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from holmes.core.llm import LLM, TokenCountMetadata
from holmes.core.models import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.utils.stream import StreamEvents
from server import app


@pytest.fixture
def client():
    return TestClient(app)


def create_mock_llm_response(
    content="I need to run a dangerous command", tool_calls=None
):
    """Create a mock LLM response"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message = MagicMock()
    mock_response.choices[0].message.content = content
    mock_response.choices[0].message.tool_calls = tool_calls
    mock_response.choices[0].message.reasoning_content = None
    mock_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (tool_calls or [])
        ]
        if tool_calls
        else None,
    }
    mock_response.to_json.return_value = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    )
    return mock_response


def create_mock_tool_call(
    tool_call_id="tool_call_123", tool_name="kubectl_delete", arguments=None
):
    """Create a mock tool call object"""
    mock_tool_call = MagicMock()
    mock_tool_call.id = tool_call_id
    mock_tool_call.function = MagicMock()
    mock_tool_call.function.name = tool_name
    mock_tool_call.function.arguments = json.dumps(
        arguments or {"command": "kubectl delete pod dangerous-pod"}
    )
    return mock_tool_call


@patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_streaming_chat_approval_workflow_requires_approval(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    mock_load_robusta_config,
    client,
):
    """Test that streaming chat requests approval for dangerous commands"""

    # Setup mocks
    mock_load_robusta_config.return_value = None
    mock_get_global_instructions.return_value = []

    # Create real ToolCallingLLM with mocked dependencies
    mock_llm = MagicMock(spec=LLM)
    mock_tool_executor = MagicMock(spec=ToolExecutor)

    # Create the actual ToolCallingLLM instance
    ai = ToolCallingLLM(tool_executor=mock_tool_executor, max_steps=5, llm=mock_llm)

    # Mock LLM methods
    mock_llm.count_tokens.return_value = TokenCountMetadata(
        total_tokens=100,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )
    mock_llm.get_context_window_size.return_value = 128000
    mock_llm.get_maximum_output_token.return_value = 4096
    mock_llm.model = "gpt-4o"

    # Mock the LLM completion to return a tool call
    mock_tool_call = create_mock_tool_call()
    mock_llm_response = create_mock_llm_response(
        content="I need to run a dangerous command", tool_calls=[mock_tool_call]
    )
    mock_llm.completion.return_value = mock_llm_response

    # Mock tool executor
    mock_tool_executor.get_all_tools_openai_format.return_value = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_delete",
                "description": "Delete Kubernetes resources",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]

    # Create a proper toolset mock with status
    mock_toolset = MagicMock()
    mock_toolset.name = "kubectl"
    mock_toolset.status = MagicMock()
    mock_toolset.status.value = "enabled"
    mock_tool_executor.toolsets = [mock_toolset]

    # Mock _invoke_tool to return approval required

    def mock_invoke_tool(
        tool_name: str,
        tool_params: dict,
        user_approved: bool,
        tool_call_id: str,
        tool_number: Optional[int] = None,
    ) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.APPROVAL_REQUIRED,
            data="Command requires approval",
            params=tool_params,
        )

    ai._directly_invoke_tool_call = mock_invoke_tool

    mock_create_toolcalling_llm.return_value = ai

    # Make streaming request
    payload = {
        "ask": "Delete the dangerous pod",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        "stream": True,
        "enable_tool_approval": True,
    }

    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200

    # Parse streaming response
    events = []
    for line in response.text.split("\n"):
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                events.append((event_type, data))
            except json.JSONDecodeError:
                pass

    # Verify we got the expected events
    event_types = [event[0] for event in events]
    assert StreamEvents.START_TOOL.value in event_types
    assert StreamEvents.TOOL_RESULT.value in event_types
    assert StreamEvents.APPROVAL_REQUIRED.value in event_types

    # Find the approval required event
    approval_event = next(
        (
            event[1]
            for event in events
            if event[0] == StreamEvents.APPROVAL_REQUIRED.value
        ),
        None,
    )
    assert approval_event is not None
    assert approval_event["requires_approval"] is True
    assert "pending_approvals" in approval_event
    assert len(approval_event["pending_approvals"]) == 1

    pending_approval = approval_event["pending_approvals"][0]
    assert pending_approval["tool_call_id"] == "tool_call_123"
    assert pending_approval["tool_name"] == "kubectl_delete"


@patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_streaming_chat_approval_workflow_approve_and_execute(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    mock_load_robusta_config,
    client,
):
    """Test that streaming chat executes approved commands"""

    # Setup mocks
    mock_load_robusta_config.return_value = None
    mock_get_global_instructions.return_value = []

    # Create real ToolCallingLLM with mocked dependencies
    mock_llm = MagicMock(spec=LLM)
    mock_tool_executor = MagicMock(spec=ToolExecutor)

    # Create the actual ToolCallingLLM instance
    ai = ToolCallingLLM(tool_executor=mock_tool_executor, max_steps=5, llm=mock_llm)

    # Mock LLM methods - Return final answer after tool execution
    mock_llm.count_tokens.return_value = TokenCountMetadata(
        total_tokens=100,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )
    mock_llm.get_context_window_size.return_value = 128000
    mock_llm.get_maximum_output_token.return_value = 4096
    mock_llm.model = "gpt-4o"

    mock_llm_response = create_mock_llm_response(
        content="Command executed successfully. The dangerous pod has been deleted.",
        tool_calls=None,  # No more tools to call
    )
    mock_llm.completion.return_value = mock_llm_response

    # Mock tool executor
    mock_tool_executor.get_all_tools_openai_format.return_value = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_delete",
                "description": "Delete Kubernetes resources",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]

    # Create a proper toolset mock with status
    mock_toolset = MagicMock()
    mock_toolset.name = "kubectl"
    mock_toolset.status = MagicMock()
    mock_toolset.status.value = "enabled"
    mock_tool_executor.toolsets = [mock_toolset]

    # Mock process_tool_decisions to simulate approval and execution
    ai.process_tool_decisions = MagicMock(
        side_effect=lambda messages, tool_decisions: (
            messages
            + [
                {
                    "tool_call_id": "tool_call_123",
                    "role": "tool",
                    "name": "kubectl_delete",
                    "content": "pod 'dangerous-pod' deleted",
                }
            ],
            [],  # Empty list for StreamMessages
        )
    )

    mock_create_toolcalling_llm.return_value = ai

    # Request with approval decision
    approval_payload = {
        "ask": "Delete the dangerous pod",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Delete the dangerous pod"},
            {
                "role": "assistant",
                "content": "I need to run a dangerous command. Let me check if this is allowed.",
                "tool_calls": [
                    {
                        "id": "tool_call_123",
                        "type": "function",
                        "function": {
                            "name": "kubectl_delete",
                            "arguments": json.dumps(
                                {"command": "kubectl delete pod dangerous-pod"}
                            ),
                        },
                    }
                ],
                "pending_approval": True,
            },
        ],
        "stream": True,
        "enable_tool_approval": True,
        "tool_decisions": [{"tool_call_id": "tool_call_123", "approved": True}],
    }

    response = client.post("/api/chat", json=approval_payload)
    assert response.status_code == 200

    # Parse streaming response
    events = []
    for line in response.text.split("\n"):
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                events.append((event_type, data))
            except json.JSONDecodeError:
                pass

    # Verify execution completed
    event_types = [event[0] for event in events]
    assert StreamEvents.ANSWER_END.value in event_types

    # Find the answer end event
    answer_event = next(
        (event[1] for event in events if event[0] == StreamEvents.ANSWER_END.value),
        None,
    )
    assert answer_event is not None
    assert "Command executed successfully" in answer_event["analysis"]

    # Verify tool execution was called
    ai.process_tool_decisions.assert_called_once()
    args, kwargs = ai.process_tool_decisions.call_args
    tool_decisions = args[1]  # Second argument is tool_decisions
    assert len(tool_decisions) == 1
    assert tool_decisions[0].tool_call_id == "tool_call_123"
    assert tool_decisions[0].approved is True


@patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_streaming_chat_approval_workflow_reject_command(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    mock_load_robusta_config,
    client,
):
    """Test that streaming chat handles rejected commands"""

    # Setup mocks
    mock_load_robusta_config.return_value = None
    mock_get_global_instructions.return_value = []

    # Create real ToolCallingLLM with mocked dependencies
    mock_llm = MagicMock(spec=LLM)
    mock_tool_executor = MagicMock(spec=ToolExecutor)

    # Create the actual ToolCallingLLM instance
    ai = ToolCallingLLM(tool_executor=mock_tool_executor, max_steps=5, llm=mock_llm)

    # Mock LLM methods - Return final answer after tool rejection
    mock_llm.count_tokens.return_value = TokenCountMetadata(
        total_tokens=100,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )
    mock_llm.get_context_window_size.return_value = 128000
    mock_llm.get_maximum_output_token.return_value = 4096
    mock_llm.model = "gpt-4o"

    mock_llm_response = create_mock_llm_response(
        content="I understand. I won't execute the dangerous command as you denied the request.",
        tool_calls=None,  # No more tools to call
    )
    mock_llm.completion.return_value = mock_llm_response

    # Mock tool executor
    mock_tool_executor.get_all_tools_openai_format.return_value = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_delete",
                "description": "Delete Kubernetes resources",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]

    # Create a proper toolset mock with status
    mock_toolset = MagicMock()
    mock_toolset.name = "kubectl"
    mock_toolset.status = MagicMock()
    mock_toolset.status.value = "enabled"
    mock_tool_executor.toolsets = [mock_toolset]

    # Mock process_tool_decisions to simulate rejection
    ai.process_tool_decisions = MagicMock(
        side_effect=lambda messages, tool_decisions: (
            messages
            + [
                {
                    "tool_call_id": "tool_call_123",
                    "role": "tool",
                    "name": "kubectl_delete",
                    "content": "Tool execution was denied by the user.",
                }
            ],
            [],  # Empty list for StreamMessages
        )
    )

    mock_create_toolcalling_llm.return_value = ai

    # Request with rejection
    rejection_payload = {
        "ask": "Delete the dangerous pod",
        "conversation_history": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Delete the dangerous pod"},
            {
                "role": "assistant",
                "content": "I need to run a dangerous command. Let me check if this is allowed.",
                "tool_calls": [
                    {
                        "id": "tool_call_123",
                        "type": "function",
                        "function": {
                            "name": "kubectl_delete",
                            "arguments": json.dumps(
                                {"command": "kubectl delete pod dangerous-pod"}
                            ),
                        },
                    }
                ],
                "pending_approval": True,
            },
        ],
        "stream": True,
        "enable_tool_approval": True,
        "tool_decisions": [{"tool_call_id": "tool_call_123", "approved": False}],
    }

    response = client.post("/api/chat", json=rejection_payload)
    assert response.status_code == 200

    # Parse streaming response to verify rejection was handled
    events = []
    for line in response.text.split("\n"):
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                events.append((event_type, data))
            except json.JSONDecodeError:
                pass

    # Verify execution completed
    event_types = [event[0] for event in events]
    assert StreamEvents.ANSWER_END.value in event_types

    # Find the answer end event
    answer_event = next(
        (event[1] for event in events if event[0] == StreamEvents.ANSWER_END.value),
        None,
    )
    assert answer_event is not None
    assert "won't execute" in answer_event["analysis"]

    # Verify tool processing was called
    ai.process_tool_decisions.assert_called_once()
    args, kwargs = ai.process_tool_decisions.call_args
    tool_decisions = args[1]  # Second argument is tool_decisions
    assert len(tool_decisions) == 1
    assert tool_decisions[0].tool_call_id == "tool_call_123"
    assert tool_decisions[0].approved is False
