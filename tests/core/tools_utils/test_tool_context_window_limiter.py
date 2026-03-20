import base64
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tools_utils.tool_context_window_limiter import (
    spill_oversized_tool_result,
)


class TestPreventOverlyBigToolResponse:
    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM instance."""
        llm = Mock(spec=LLM)
        llm.get_context_window_size.return_value = 4096
        llm.get_max_token_count_for_single_tool.return_value = (
            2048  # Default to 50% of context window
        )
        llm.count_tokens.return_value = ContextWindowUsage(
            total_tokens=1000,
            system_tokens=0,
            tools_to_call_tokens=0,
            tools_tokens=0,
            user_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )
        return llm

    @pytest.fixture
    def success_tool_call_result(self):
        """Create a successful tool call result."""
        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="Some successful output data",
        )
        return ToolCallResult(
            tool_call_id="test-id-1",
            tool_name="test_tool",
            description="Test tool description",
            result=result,
        )

    def test_within_token_limit(self, mock_llm, success_tool_call_result):
        """Test that function does nothing when tool result is within token limit."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            50,
        ):
            # Context window: 4096, 50% = 2048 tokens allowed
            # Token count: 1000 (within limit)
            mock_llm.get_max_token_count_for_single_tool.return_value = 2048
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=1000,
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            original_status = success_tool_call_result.result.status
            original_data = success_tool_call_result.result.data
            original_error = success_tool_call_result.result.error

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            # Should remain unchanged
            assert success_tool_call_result.result.status == original_status
            assert success_tool_call_result.result.data == original_data
            assert success_tool_call_result.result.error == original_error

    def test_exceeds_token_limit(self, mock_llm, success_tool_call_result):
        """Test that function modifies result when tool result exceeds token limit."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            50,
        ):
            # Context window: 4096, 50% = 2048 tokens allowed
            # Token count: 3000 (exceeds limit)
            mock_llm.get_max_token_count_for_single_tool.return_value = 2048
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=3000,
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            # Should be modified
            assert (
                success_tool_call_result.result.status
                == StructuredToolResultStatus.ERROR
            )
            assert success_tool_call_result.result.data is None
            assert "too large to return" in success_tool_call_result.result.error
            assert "3000/2048 tokens" in success_tool_call_result.result.error

    def test_token_calculation_accuracy(self, mock_llm, success_tool_call_result):
        """Test that token calculations are accurate."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            25,
        ):
            # Context window: 4096, 25% = 1024 tokens allowed
            # Token count: 2000 (exceeds limit)
            mock_llm.get_context_window_size.return_value = 4096
            mock_llm.get_max_token_count_for_single_tool.return_value = 1024
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=2000,
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            assert "2000/1024 tokens" in success_tool_call_result.result.error

    def test_message_construction_calls_to_llm_message(
        self, mock_llm, success_tool_call_result
    ):
        """Test that the function calls to_llm_message to get the message for token counting."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            50,
        ):
            mock_llm.get_max_token_count_for_single_tool.return_value = 2048
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=1000,  # Within limit
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            # Verify that count_tokens was called with a list containing one message
            call_args = mock_llm.count_tokens.call_args
            assert (
                len(call_args[1]["messages"]) == 1
            )  # Should be called with messages kwarg containing 1 message

    def test_different_context_window_sizes(self, mock_llm, success_tool_call_result):
        """Test with different context window sizes."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            40,
        ):
            # Test with smaller context window
            mock_llm.get_context_window_size.return_value = 2048
            mock_llm.get_max_token_count_for_single_tool.return_value = (
                819  # 40% of 2048
            )
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=1000,  # 40% of 2048 = 819 tokens allowed, 1000 exceeds this
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            assert (
                success_tool_call_result.result.status
                == StructuredToolResultStatus.ERROR
            )
            assert "1000/819 tokens" in success_tool_call_result.result.error

    def test_edge_case_exactly_at_limit(self, mock_llm, success_tool_call_result):
        """Test behavior when token count is exactly at the limit."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            50,
        ):
            mock_llm.get_context_window_size.return_value = 4096
            mock_llm.get_max_token_count_for_single_tool.return_value = 2048
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=2048,  # Exactly 50% of 4096
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            original_status = success_tool_call_result.result.status
            original_data = success_tool_call_result.result.data

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            # Should remain unchanged (not > max_tokens_allowed)
            assert success_tool_call_result.result.status == original_status
            assert success_tool_call_result.result.data == original_data

    def test_error_message_format(self, mock_llm, success_tool_call_result):
        """Test that error message contains all expected components."""
        with patch(
            "holmes.common.env_vars.TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT",
            20,
        ):
            mock_llm.get_context_window_size.return_value = 5000
            mock_llm.get_max_token_count_for_single_tool.return_value = (
                1000  # 20% of 5000
            )
            mock_llm.count_tokens.return_value = ContextWindowUsage(
                total_tokens=2000,  # 20% of 5000 = 1000 tokens allowed
                system_tokens=0,
                tools_to_call_tokens=0,
                tools_tokens=0,
                user_tokens=0,
                assistant_tokens=0,
                other_tokens=0,
            )

            spill_oversized_tool_result(success_tool_call_result, mock_llm)

            error_msg = success_tool_call_result.result.error
            assert "The tool call result is too large to return" in error_msg
            assert "2000/1000 tokens" in error_msg
            assert "Try to repeat the query" in error_msg
            assert "narrow down the result" in error_msg

    def test_spill_to_disk_with_images(self, mock_llm, tmp_path):
        """When result exceeds limit and has images, images are saved to disk."""
        pixel_bytes = b"\x89PNG\r\n\x1a\nfake"
        pixel_b64 = base64.b64encode(pixel_bytes).decode()
        images = [{"data": pixel_b64, "mimeType": "image/png"}]

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="large output " * 500,
            images=images,
        )
        tcr = ToolCallResult(
            tool_call_id="call-img-1",
            tool_name="vision_tool",
            description="desc",
            result=result,
        )

        mock_llm.get_max_token_count_for_single_tool.return_value = 100
        mock_llm.count_tokens.return_value = ContextWindowUsage(
            total_tokens=5000,
            system_tokens=0,
            tools_to_call_tokens=0,
            tools_tokens=0,
            user_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )

        spill_oversized_tool_result(tcr, mock_llm, tool_results_dir=tmp_path)

        # Data should be replaced with pointer message
        assert "Saved to:" in tcr.result.data
        assert "too large to return" in tcr.result.data
        # Images should be cleared from the result (saved to disk instead)
        assert tcr.result.images is None
        # Image file should exist on disk
        assert "Images saved to disk" in tcr.result.data
        assert "read_image_file" in tcr.result.data
        # Verify the actual image file was written
        img_files = list(tmp_path.glob("*.png"))
        assert len(img_files) == 1
        assert img_files[0].read_bytes() == pixel_bytes

    def test_spill_to_disk_without_images(self, mock_llm, tmp_path):
        """When result exceeds limit without images, no image references in pointer."""
        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="big output " * 500,
        )
        tcr = ToolCallResult(
            tool_call_id="call-txt-1",
            tool_name="text_tool",
            description="desc",
            result=result,
        )

        mock_llm.get_max_token_count_for_single_tool.return_value = 100
        mock_llm.count_tokens.return_value = ContextWindowUsage(
            total_tokens=5000,
            system_tokens=0,
            tools_to_call_tokens=0,
            tools_tokens=0,
            user_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )

        spill_oversized_tool_result(tcr, mock_llm, tool_results_dir=tmp_path)

        assert "Saved to:" in tcr.result.data
        assert "Images saved to disk" not in tcr.result.data
        assert "read_image_file" not in tcr.result.data
