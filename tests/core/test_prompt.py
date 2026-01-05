import re
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

import pytest
from rich.console import Console

from holmes.config import Config
from holmes.core.conversations import (
    build_chat_messages,
    build_issue_chat_messages,
    build_workload_health_chat_messages,
)
from holmes.core.investigation import get_investigation_context
from holmes.core.models import (
    InvestigateRequest,
    IssueChatRequest,
    IssueInvestigationResult,
    WorkloadHealthChatRequest,
    WorkloadHealthInvestigationResult,
)
from holmes.core.prompt import (
    append_all_files_to_user_prompt,
    append_file_to_user_prompt,
    build_initial_ask_messages,
    generate_user_prompt,
    get_tasks_management_system_reminder,
)
from holmes.utils.global_instructions import generate_runbooks_args


class DummyRunbookCatalog:
    """Mock runbook catalog for testing."""

    def to_prompt_string(self):
        return "RUNBOOK CATALOG PROMPT"


class DummyInstructions:
    def __init__(self, instructions):
        self.instructions = instructions


@pytest.fixture
def console():
    return Console(force_terminal=False, force_jupyter=False)


@pytest.fixture
def mock_tool_executor():
    tool_executor = Mock()
    tool_executor.toolsets = []
    return tool_executor


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock config for testing."""
    config = Mock(spec=Config)
    config.cluster_name = "test-cluster"
    config.get_runbook_catalog = Mock(return_value=None)
    return config


@pytest.fixture
def mock_ai(mock_tool_executor):
    """Create a mock AI/LLM instance."""
    ai = Mock()
    ai.tool_executor = mock_tool_executor
    ai.llm = Mock()
    ai.llm.get_context_window_size = Mock(return_value=128000)
    ai.llm.count_tokens = Mock(return_value=Mock(total_tokens=1000))
    ai.llm.get_maximum_output_token = Mock(return_value=4096)
    return ai


@pytest.fixture
def mock_dal():
    """Create a mock DAL instance."""
    dal = Mock()
    dal.get_global_instructions_for_account = Mock(return_value=None)
    dal.get_resource_instructions = Mock(return_value=None)
    dal.get_issue_data = Mock(return_value=None)
    dal.get_workload_issues = Mock(return_value=[])
    return dal


def get_user_message_from_messages(messages: list, get_last: bool = False) -> str:
    """Extract user message content from messages list.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        get_last: If True, return the last user message (for conversation history).
                  If False, assert exactly one user message exists.

    Returns:
        Content of the user message

    Raises:
        AssertionError: If no user message found, or if get_last=False and multiple user messages found
    """
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert len(user_messages) > 0, "No user messages found in messages list"

    if get_last:
        return user_messages[-1]["content"]
    else:
        assert (
            len(user_messages) == 1
        ), f"Expected exactly one user message, found {len(user_messages)}"
        return user_messages[0]["content"]


def create_test_files(file_paths: list, tmp_path: Path) -> Optional[list]:
    """Create test files in temporary directory.

    Args:
        file_paths: List of file names to create
        tmp_path: Temporary directory path

    Returns:
        List of Path objects for created files, or None if no files to create
    """
    if not file_paths:
        return None

    test_files = []
    for file_name in file_paths:
        test_file = tmp_path / file_name
        test_file.write_text(f"Content of {file_name}")
        test_files.append(test_file)
    return test_files


def create_mock_investigator():
    """Create a mock investigator instance for testing."""
    mock_investigator = Mock()
    mock_investigator.tool_executor = Mock()
    mock_investigator.tool_executor.toolsets = []
    mock_investigator.runbook_manager = Mock()
    mock_investigator.runbook_manager.get_instructions_for_issue = Mock(return_value=[])
    mock_investigator.llm = Mock()
    mock_investigator.llm.model = None
    return mock_investigator


def extract_instructions(instructions_obj):
    """Extract instruction list from DummyInstructions object or return None."""
    return instructions_obj.instructions if instructions_obj else None


def create_issue_chat_request(user_ask: str, issue_type: str = "prometheus"):
    """Create an IssueChatRequest for testing."""
    return IssueChatRequest(
        ask=user_ask,
        conversation_history=None,
        investigation_result=IssueInvestigationResult(
            result="Investigation analysis",
            tools=[],
        ),
        issue_type=issue_type,
    )


def create_workload_health_chat_request(user_ask: str, resource: Optional[dict] = None):
    """Create a WorkloadHealthChatRequest for testing."""
    if resource is None:
        resource = {"kind": "Deployment", "name": "my-app"}

    return WorkloadHealthChatRequest(
        ask=user_ask,
        conversation_history=None,
        workload_health_result=WorkloadHealthInvestigationResult(
            analysis="Workload is healthy",
            tools=[],
        ),
        resource=resource,
    )


def assert_user_prompt_contains_timestamp(user_prompt: str):
    """Assert that user prompt contains the UTC timestamp in seconds."""
    timestamp_pattern = r"The current UTC timestamp in seconds is (\d+)\."
    match = re.search(timestamp_pattern, user_prompt)
    assert match is not None, (
        f"User prompt does not contain UTC timestamp in seconds. "
        f"Expected pattern: 'The current UTC timestamp in seconds is <number>.'\n"
        f"User prompt content:\n{user_prompt}"
    )
    timestamp_value = int(match.group(1))
    assert (
        946684800 <= timestamp_value <= 32503680000
    ), f"Timestamp value {timestamp_value} is outside reasonable range"
    return timestamp_value


def validate_user_prompt(
    user_content: str,
    original_prompt: str,
    expected_runbooks: bool = False,
    expected_global_instructions: Optional[list] = None,
    expected_issue_instructions: Optional[list] = None,
    expected_resource_instructions: Optional[list] = None,
):
    """Validate user prompt contains expected components."""
    assert (
        original_prompt in user_content
    ), f"Original prompt '{original_prompt}' not found in user content"
    assert_user_prompt_contains_timestamp(user_content)

    if expected_runbooks:
        assert (
            "RUNBOOK CATALOG PROMPT" in user_content
        ), "Runbook catalog not found when expected"

    if expected_global_instructions:
        for instruction in expected_global_instructions:
            assert (
                instruction in user_content
            ), f"Global instruction '{instruction}' not found"

    if expected_issue_instructions:
        for instruction in expected_issue_instructions:
            assert (
                f"* {instruction}" in user_content
            ), f"Issue instruction '{instruction}' not found"

    if expected_resource_instructions:
        for instruction in expected_resource_instructions:
            assert (
                f"* {instruction}" in user_content
            ), f"Resource instruction '{instruction}' not found"


class TestBuildInitialAskMessages:
    """Test user prompt validation for build_initial_ask_messages flows."""

    @pytest.mark.parametrize(
        "user_prompt,file_paths,runbooks",
        [
            ("What's wrong with my pod?", None, None),
            ("Analyze this file", ["test.txt"], None),
            ("What should I check?", None, DummyRunbookCatalog()),
            ("Complex case", ["file.txt"], DummyRunbookCatalog()),
        ],
    )
    def test_ask_command_user_prompt(
        self,
        console,
        mock_tool_executor,
        tmp_path,
        user_prompt,
        file_paths,
        runbooks,
    ):
        """Test user prompt in ask command flow with various configurations."""
        test_files = create_test_files(file_paths, tmp_path)

        messages = build_initial_ask_messages(
            console,
            user_prompt,
            test_files,
            mock_tool_executor,
            runbooks,
            None,
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"

        user_content = get_user_message_from_messages(messages)

        validate_user_prompt(
            user_content,
            user_prompt,
            expected_runbooks=runbooks is not None,
        )

        assert get_tasks_management_system_reminder() in user_content

        if test_files:
            for test_file in test_files:
                assert test_file.read_text() in user_content
                assert "<attached-file" in user_content

    def test_build_initial_ask_messages_with_system_prompt_additions(
        self, console, mock_tool_executor
    ):
        """Test message building with system prompt additions."""
        system_additions = "Additional system instructions here."
        messages = build_initial_ask_messages(
            console,
            "Test prompt",
            None,
            mock_tool_executor,
            None,
            system_additions,
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "Additional" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        user_content = messages[1]["content"]
        assert "Test prompt" in user_content
        assert get_tasks_management_system_reminder() in user_content
        assert "The current UTC timestamp in seconds is" in user_content


class TestServerFlows:
    """Test user prompt validation for flows from server.py."""

    @pytest.mark.parametrize(
        "user_ask,global_instructions,runbooks,conversation_history",
        [
            ("Show me the logs", None, None, None),
            ("What's happening?", DummyInstructions(["Always check CPU"]), None, None),
            ("Help me debug", None, DummyRunbookCatalog(), None),
            (
                "Complex chat",
                DummyInstructions(["Global rule"]),
                DummyRunbookCatalog(),
                None,
            ),
            (
                "Follow up question",
                None,
                None,
                [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What's the status?"},
                    {"role": "assistant", "content": "Everything looks good."},
                ],
            ),
            (
                "Another question",
                DummyInstructions(["Check logs"]),
                DummyRunbookCatalog(),
                [
                    {"role": "system", "content": "System prompt"},
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "Answer to first"},
                ],
            ),
        ],
    )
    def test_chat_api_user_prompt(
        self,
        mock_ai,
        mock_config,
        user_ask,
        global_instructions,
        runbooks,
        conversation_history,
    ):
        """Test user prompt in /api/chat flow with various configurations."""
        messages = build_chat_messages(
            ask=user_ask,
            conversation_history=conversation_history,
            ai=mock_ai,
            config=mock_config,
            global_instructions=global_instructions,
            additional_system_prompt=None,
            runbooks=runbooks,
        )

        user_content = get_user_message_from_messages(
            messages, get_last=True if conversation_history else False
        )

        validate_user_prompt(
            user_content,
            user_ask,
            expected_runbooks=runbooks is not None,
            expected_global_instructions=extract_instructions(global_instructions),
        )

    @pytest.mark.parametrize(
        "user_ask,global_instructions",
        [
            ("Tell me more about this alert", None),
            ("What should I do?", DummyInstructions(["Check metrics"])),
        ],
    )
    def test_issue_chat_api_user_prompt(
        self,
        mock_ai,
        mock_config,
        user_ask,
        global_instructions,
    ):
        """Test user prompt in /api/issue_chat flow."""
        issue_chat_request = create_issue_chat_request(user_ask)

        messages = build_issue_chat_messages(
            issue_chat_request=issue_chat_request,
            ai=mock_ai,
            config=mock_config,
            global_instructions=global_instructions,
            runbooks=None,
        )

        user_content = get_user_message_from_messages(messages)
        validate_user_prompt(
            user_content,
            user_ask,
            expected_global_instructions=extract_instructions(global_instructions),
        )

    def test_workload_health_chat_user_prompt(self, mock_ai, mock_config):
        """Test user prompt in /api/workload_health_chat flow."""
        user_ask = "Why is my pod unhealthy?"
        workload_health_chat_request = create_workload_health_chat_request(user_ask)

        messages = build_workload_health_chat_messages(
            workload_health_chat_request=workload_health_chat_request,
            ai=mock_ai,
            config=mock_config,
            global_instructions=None,
            runbooks=None,
        )

        user_content = get_user_message_from_messages(messages)
        validate_user_prompt(user_content, user_ask)

    @pytest.mark.parametrize(
        "user_ask,global_instructions,issue_instructions",
        [
            ("Check health", None, None),
            (
                "Check health with instructions",
                DummyInstructions(["Verify replicas"]),
                ["Check pod status"],
            ),
        ],
    )
    def test_workload_health_check_user_prompt(
        self,
        user_ask,
        global_instructions,
        issue_instructions,
    ):
        """Test user prompt in /api/workload_health_check flow."""
        runbooks_ctx = generate_runbooks_args(
            runbook_catalog=None,
            global_instructions=global_instructions,
            issue_instructions=issue_instructions,
        )

        final_prompt = generate_user_prompt(user_ask, runbooks_ctx)

        validate_user_prompt(
            final_prompt,
            user_ask,
            expected_global_instructions=extract_instructions(global_instructions),
            expected_issue_instructions=issue_instructions,
        )


class TestInvestigationFlow:
    """Test user prompt validation for investigation flow."""

    def test_investigate_api_user_prompt(self, mock_config, mock_dal):
        """Test user prompt in /api/investigate flow."""
        mock_investigator = create_mock_investigator()
        mock_config.create_issue_investigator = Mock(return_value=mock_investigator)

        investigate_request = InvestigateRequest(
            title="Test Alert",
            description="Test description",
            source="prometheus",
            subject={},
            context={"robusta_issue_id": "test-123"},
            prompt_template="builtin://generic_investigation.jinja2",
        )

        _, _, user_prompt, _, _, _ = get_investigation_context(
            investigate_request, mock_dal, mock_config
        )

        assert user_prompt is not None
        assert isinstance(user_prompt, str)
        assert "context from the issue" in user_prompt.lower()
        assert_user_prompt_contains_timestamp(user_prompt)


class TestUserPromptComponents:
    """Test that user prompts include all expected components via generate_user_prompt."""

    @pytest.mark.parametrize(
        "user_prompt,runbook_catalog,global_instructions,issue_instructions,resource_instructions",
        [
            ("My question", None, None, None, None),
            ("Help me", DummyRunbookCatalog(), None, None, None),
            ("Question", None, DummyInstructions(["Global rule 1"]), None, None),
            ("Investigate", None, None, ["Step 1"], None),
            (
                "Complex",
                DummyRunbookCatalog(),
                DummyInstructions(["Global"]),
                ["Issue step"],
                SimpleNamespace(instructions=["Resource step"], documents=[]),
            ),
        ],
    )
    def test_generate_user_prompt_components(
        self,
        user_prompt,
        runbook_catalog,
        global_instructions,
        issue_instructions,
        resource_instructions,
    ):
        """Test generate_user_prompt includes all components conditionally."""
        ctx = generate_runbooks_args(
            runbook_catalog=runbook_catalog,
            global_instructions=global_instructions,
            issue_instructions=issue_instructions,
            resource_instructions=resource_instructions,
        )

        final_prompt = generate_user_prompt(user_prompt, ctx)

        expected_resource_instructions = (
            resource_instructions.instructions if resource_instructions else None
        )

        validate_user_prompt(
            final_prompt,
            user_prompt,
            expected_runbooks=runbook_catalog is not None,
            expected_global_instructions=extract_instructions(global_instructions),
            expected_issue_instructions=issue_instructions,
            expected_resource_instructions=expected_resource_instructions,
        )


def test_append_file_to_user_prompt(tmp_path):
    """Test appending a single file to user prompt."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("Test file content")

    prompt = "Original prompt"
    result = append_file_to_user_prompt(prompt, test_file)

    assert "Original prompt" in result
    assert "Test file content" in result
    # Check for file attachment markers
    assert "<attached-file" in result
    assert "test.txt" in result
    assert "</attached-file>" in result


def test_append_all_files_to_user_prompt(console, tmp_path):
    """Test appending multiple files to user prompt."""
    # Create multiple test files
    file1 = tmp_path / "file1.txt"
    file1.write_text("Content 1")

    file2 = tmp_path / "file2.txt"
    file2.write_text("Content 2")

    prompt = "Original prompt"
    result = append_all_files_to_user_prompt(console, prompt, [file1, file2])

    assert "Original prompt" in result
    assert "Content 1" in result
    assert "Content 2" in result
    # Check for file attachment markers
    assert "<attached-file" in result
    assert "file1.txt" in result
    assert "file2.txt" in result
    assert result.count("</attached-file>") == 2


def test_append_all_files_to_user_prompt_no_files(console):
    """Test appending files when no files are provided."""
    prompt = "Original prompt"
    result = append_all_files_to_user_prompt(console, prompt, None)

    assert result == "Original prompt"

    # Also test with empty list
    result = append_all_files_to_user_prompt(console, prompt, [])
    assert result == "Original prompt"
