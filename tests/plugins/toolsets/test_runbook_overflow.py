"""
TDD Red Tests for Runbook Fetcher Context Overflow

These tests expose bugs where large runbook content can cause context window
overflow. The fetch_runbook tool returns runbook content WITHOUT any size limit,
and wraps it with additional instructions (~2KB), further increasing size.

Key issues:
1. Runbook content from DAL (Supabase) has no size limit
2. MD file runbooks have no size limit
3. The XML wrapper adds significant overhead
4. Multiple runbook fetches accumulate without limit
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.runbooks import RobustaRunbookInstruction
from holmes.plugins.toolsets.runbook.runbook_fetcher import (
    RunbookFetcher,
    RunbookToolset,
)
from tests.conftest import create_mock_tool_invoke_context


class TestRunbookFetcherOverflow:
    """Tests for runbook content causing context overflow."""

    def test_large_robusta_runbook_should_be_truncated(self):
        """
        When fetching a runbook from Supabase DAL that has very large content,
        the content should be truncated to prevent context window overflow.

        Current behavior: The entire content is returned, potentially causing
        "prompt is too long" errors.
        """
        # Create a mock DAL that returns a huge runbook
        mock_dal = MagicMock()
        mock_dal.enabled = True

        # Create a runbook with very large instruction content (100K chars)
        large_instruction = "Step 1: " + "Check the logs. " * 10000
        large_runbook = RobustaRunbookInstruction(
            id="test-runbook-id",
            symptom="System is slow",
            title="Performance Troubleshooting",
            instruction=large_instruction,
        )
        mock_dal.get_runbook_content.return_value = large_runbook

        toolset = RunbookToolset(dal=mock_dal)
        runbook_fetcher = RunbookFetcher(
            toolset=toolset,
            dal=mock_dal,
        )

        # Fetch the runbook
        result = runbook_fetcher._invoke(
            {"runbook_id": "test-runbook-id"},
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None

        # The result should be limited to prevent context overflow
        # With a 200K token limit and ~4 chars/token, individual tool results
        # should be much smaller (e.g., 25K tokens = 100K chars max)
        MAX_RUNBOOK_RESULT_CHARS = 100000

        assert len(result.data) <= MAX_RUNBOOK_RESULT_CHARS, (
            f"Runbook content too large: {len(result.data)} chars. "
            f"Expected max {MAX_RUNBOOK_RESULT_CHARS} chars to prevent overflow."
        )

    def test_large_md_runbook_should_be_truncated(self):
        """
        When fetching a markdown runbook that has very large content,
        the content should be truncated.
        """
        # Create a temporary directory with a large markdown file
        with tempfile.TemporaryDirectory() as tmpdir:
            runbook_path = Path(tmpdir) / "large_runbook.md"

            # Create a 200KB markdown file
            large_content = "# Troubleshooting Guide\n\n"
            large_content += "## Step 1\n" + "Check the system status. " * 10000
            large_content += "\n\n## Step 2\n" + "Review the logs carefully. " * 10000

            runbook_path.write_text(large_content)

            toolset = RunbookToolset(
                dal=None,
                additional_search_paths=[tmpdir],
            )
            runbook_fetcher = RunbookFetcher(
                toolset=toolset,
                additional_search_paths=[tmpdir],
                dal=None,
            )

            result = runbook_fetcher._invoke(
                {"runbook_id": "large_runbook.md"},
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data is not None

            # Check the wrapped content size (includes XML tags and instructions)
            MAX_RUNBOOK_RESULT_CHARS = 100000

            assert len(result.data) <= MAX_RUNBOOK_RESULT_CHARS, (
                f"MD runbook result too large: {len(result.data)} chars. "
                f"The XML wrapper adds ~2KB overhead on top of content."
            )

    def test_runbook_wrapper_overhead_is_reasonable(self):
        """
        The runbook fetcher wraps content in XML and adds instructions.
        This overhead should be reasonable and predictable.

        This is a documentation/regression test - the current overhead
        is acceptable, but large content still needs truncation.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            runbook_path = Path(tmpdir) / "small_runbook.md"

            # Create a moderate-size runbook (50KB)
            content = "# Guide\n\n" + "Instructions here. " * 3000
            runbook_path.write_text(content)

            toolset = RunbookToolset(dal=None, additional_search_paths=[tmpdir])
            runbook_fetcher = RunbookFetcher(
                toolset=toolset,
                additional_search_paths=[tmpdir],
                dal=None,
            )

            result = runbook_fetcher._invoke(
                {"runbook_id": "small_runbook.md"},
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS

            # The wrapper adds XML tags and example instructions
            # Verify the overhead is reasonable and documented
            original_size = len(content)
            result_size = len(result.data)
            overhead = result_size - original_size

            # Overhead from wrapper should be predictable
            EXPECTED_WRAPPER_OVERHEAD = 2500  # ~2.5KB for XML + instructions

            assert overhead <= EXPECTED_WRAPPER_OVERHEAD * 1.2, (
                f"Wrapper overhead ({overhead} chars) exceeds expected "
                f"({EXPECTED_WRAPPER_OVERHEAD} chars). This affects context budget."
            )


class TestMultipleRunbookFetches:
    """Tests for multiple runbook fetches causing accumulated overflow."""

    def test_multiple_runbook_fetches_should_be_tracked(self):
        """
        When multiple runbooks are fetched in one investigation, the total
        size should be tracked to prevent combined overflow.

        In practice, LLM might call fetch_runbook multiple times:
        - fetch_runbook("kubernetes_troubleshooting.md")
        - fetch_runbook("pod_debugging.md")
        - fetch_runbook("network_issues.md")

        Each returns ~20-50KB, combined can exceed context limits.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple runbooks
            runbook_contents = []
            for i in range(3):
                runbook_path = Path(tmpdir) / f"runbook_{i}.md"
                content = f"# Runbook {i}\n\n" + f"Step details for runbook {i}. " * 2000
                runbook_path.write_text(content)
                runbook_contents.append(content)

            toolset = RunbookToolset(dal=None, additional_search_paths=[tmpdir])
            runbook_fetcher = RunbookFetcher(
                toolset=toolset,
                additional_search_paths=[tmpdir],
                dal=None,
            )

            # Fetch all runbooks (simulating multiple tool calls)
            total_size = 0
            for i in range(3):
                result = runbook_fetcher._invoke(
                    {"runbook_id": f"runbook_{i}.md"},
                    context=create_mock_tool_invoke_context(),
                )
                assert result.status == StructuredToolResultStatus.SUCCESS
                total_size += len(result.data)

            # Combined size should be managed
            # With 3 runbooks of ~20KB each + wrappers, total ~75KB
            # This is reasonable for a single conversation

            # However, without tracking, each runbook is returned in full
            # and the tool_calling_llm only truncates AFTER the fact

            # There should be a mechanism to track cumulative runbook size
            # and warn/limit when approaching context limits
            MAX_COMBINED_RUNBOOK_CHARS = 150000  # Allow generous room

            # This assertion documents the expected behavior
            # Current implementation doesn't track this
            assert total_size <= MAX_COMBINED_RUNBOOK_CHARS, (
                f"Combined runbook size ({total_size} chars) is large. "
                f"Multiple runbook fetches should be tracked."
            )


class TestRunbookDALContentValidation:
    """Tests for validating runbook content from DAL before returning."""

    def test_dal_runbook_content_should_be_validated(self):
        """
        Runbook content fetched from Supabase DAL should be validated
        before being returned to prevent malformed/huge content from
        causing issues.
        """
        mock_dal = MagicMock()
        mock_dal.enabled = True

        # Simulate a malformed runbook with extremely large content
        # (could happen due to data corruption or malicious input)
        huge_instruction = "A" * 500000  # 500KB - way too large

        mock_dal.get_runbook_content.return_value = RobustaRunbookInstruction(
            id="malformed-runbook",
            symptom="Test symptom",
            title="Test Title",
            instruction=huge_instruction,
        )

        toolset = RunbookToolset(dal=mock_dal)
        fetcher = RunbookFetcher(toolset=toolset, dal=mock_dal)

        result = fetcher._invoke(
            {"runbook_id": "malformed-runbook"},
            context=create_mock_tool_invoke_context(),
        )

        # Even with huge content, result should be bounded
        if result.status == StructuredToolResultStatus.SUCCESS:
            MAX_RESULT_CHARS = 100000
            assert len(result.data) <= MAX_RESULT_CHARS, (
                f"Malformed runbook returned {len(result.data)} chars. "
                f"DAL content should be validated/truncated."
            )
