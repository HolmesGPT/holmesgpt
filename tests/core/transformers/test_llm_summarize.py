"""
Unit tests for LLMSummarizeTransformer.
"""

from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from holmes.core.tools import ToolInvokeContext
from holmes.core.transformers.base import TransformerError
from holmes.core.transformers.llm_summarize import LLMSummarizeTransformer
from tests.conftest import create_mock_tool_invoke_context


class TestLLMSummarizeTransformer:
    """Test cases for LLMSummarizeTransformer class."""

    def create_mock_llm(self, response_content: str = "Summarized content"):
        """Create a mock LLM that returns the specified response."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_choice = Mock()
        mock_message = Mock()

        mock_message.content = response_content
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        mock_llm.completion.return_value = mock_response

        return mock_llm

    def test_init_with_no_config(self):
        """Test transformer initialization without config."""
        transformer = LLMSummarizeTransformer()
        assert transformer.input_threshold == 1000  # default value
        assert transformer.prompt is None  # default value
        assert transformer.fast_model is None  # default value
        assert transformer.api_key is None  # default value
        assert transformer._fast_llm is None

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_init_with_fast_model_config(self, mock_default_llm):
        """Test transformer initialization with fast_model config."""
        mock_llm_instance = self.create_mock_llm()
        mock_default_llm.return_value = mock_llm_instance

        transformer = LLMSummarizeTransformer(
            input_threshold=500,
            prompt="Custom prompt",
            fast_model="gpt-4o-mini",
            api_key="test-key",
        )

        assert transformer.input_threshold == 500
        assert transformer.prompt == "Custom prompt"
        assert transformer.fast_model == "gpt-4o-mini"
        assert transformer.api_key == "test-key"
        assert transformer._fast_llm is mock_llm_instance
        mock_default_llm.assert_called_once_with("gpt-4o-mini", "test-key")

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_init_with_fast_model_no_api_key(self, mock_default_llm):
        """Test transformer initialization with fast_model but no API key."""
        mock_llm_instance = self.create_mock_llm()
        mock_default_llm.return_value = mock_llm_instance

        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        assert transformer._fast_llm is mock_llm_instance
        mock_default_llm.assert_called_once_with("gpt-4o-mini", None)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_init_fast_model_creation_failure(self, mock_default_llm):
        """Test transformer initialization handles LLM creation failure."""
        mock_default_llm.side_effect = Exception("LLM creation failed")

        transformer = LLMSummarizeTransformer(fast_model="invalid-model")

        assert transformer._fast_llm is None
        mock_default_llm.assert_called_once_with("invalid-model", None)

    def test_name_property(self):
        """Test transformer name property."""
        transformer = LLMSummarizeTransformer()
        assert transformer.name == "llm_summarize"

    def test_config_validation_success(self):
        """Test successful config validation."""
        with patch("holmes.core.transformers.llm_summarize.DefaultLLM"):
            transformer = LLMSummarizeTransformer(
                input_threshold=100,
                prompt="Valid prompt",
                fast_model="gpt-4o-mini",
            )
            assert transformer.input_threshold == 100
            assert transformer.prompt == "Valid prompt"
            assert transformer.fast_model == "gpt-4o-mini"

    def test_config_validation_invalid_threshold(self):
        """Test config validation with invalid threshold."""
        # Negative threshold
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(input_threshold=-1)

        # Non-integer threshold
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(input_threshold="invalid")

        # Non-integer threshold (float) - Pydantic v2 will reject this
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(input_threshold=100.5)

    def test_config_validation_invalid_prompt(self):
        """Test config validation with invalid prompt."""
        # Empty prompt
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(prompt="")

        # Non-string prompt
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(prompt=123)

    def test_config_validation_invalid_fast_model(self):
        """Test config validation with invalid fast_model."""
        # Empty fast_model
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(fast_model="")

        # Non-string fast_model
        with pytest.raises(ValidationError):
            LLMSummarizeTransformer(fast_model=123)

    def test_should_apply_no_fast_model(self):
        """Test should_apply when no fast model is configured."""
        transformer = LLMSummarizeTransformer()

        # Should not apply regardless of input length
        assert not transformer.should_apply("short")
        assert not transformer.should_apply("a" * 2000)  # Very long input

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_should_apply_with_fast_model_default_threshold(self, mock_default_llm):
        """Test should_apply with fast model and default threshold."""
        mock_default_llm.return_value = self.create_mock_llm()
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        # Default threshold is 1000 characters
        short_text = "a" * 999
        long_text = "a" * 1001

        assert not transformer.should_apply(short_text)
        assert transformer.should_apply(long_text)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_should_apply_with_custom_threshold(self, mock_default_llm):
        """Test should_apply with custom threshold."""
        mock_default_llm.return_value = self.create_mock_llm()
        transformer = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", input_threshold=500
        )

        short_text = "a" * 499
        exact_threshold = "a" * 500
        long_text = "a" * 501

        assert not transformer.should_apply(short_text)
        assert not transformer.should_apply(exact_threshold)  # <= threshold, not >
        assert transformer.should_apply(long_text)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_success_default_prompt(self, mock_default_llm):
        """Test successful transformation with default prompt."""
        mock_llm = self.create_mock_llm("This is a summary")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        input_text = "Original content to be summarized"
        result = transformer.transform(input_text)

        assert result == "This is a summary"

        # Verify the LLM was called with correct prompt
        mock_llm.completion.assert_called_once()
        call_args = mock_llm.completion.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0]["role"] == "user"
        assert "Original content to be summarized" in call_args[0]["content"]
        assert transformer.DEFAULT_PROMPT in call_args[0]["content"]

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_success_custom_prompt(self, mock_default_llm):
        """Test successful transformation with custom prompt."""
        mock_llm = self.create_mock_llm("Custom summary")
        mock_default_llm.return_value = mock_llm
        custom_prompt = "Summarize this in a custom way"
        transformer = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", prompt=custom_prompt
        )

        input_text = "Content to summarize"
        result = transformer.transform(input_text)

        assert result == "Custom summary"

        # Verify custom prompt was used
        call_args = mock_llm.completion.call_args[0][0]
        assert custom_prompt in call_args[0]["content"]
        assert transformer.DEFAULT_PROMPT not in call_args[0]["content"]

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_strips_whitespace(self, mock_default_llm):
        """Test that transformation strips whitespace from response."""
        mock_llm = self.create_mock_llm("  Summary with whitespace  ")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        result = transformer.transform("input text")
        assert result == "Summary with whitespace"

    def test_transform_no_fast_model(self):
        """Test transform fails when no fast model is configured."""
        transformer = LLMSummarizeTransformer()

        with pytest.raises(
            TransformerError, match="Cannot transform: no fast model configured"
        ):
            transformer.transform("input text")

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_empty_response(self, mock_default_llm):
        """Test transform fails when model returns empty response."""
        mock_llm = self.create_mock_llm("")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        with pytest.raises(TransformerError, match="Fast model returned empty summary"):
            transformer.transform("input text")

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_whitespace_only_response(self, mock_default_llm):
        """Test transform fails when model returns whitespace-only response."""
        mock_llm = self.create_mock_llm("   ")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        with pytest.raises(TransformerError, match="Fast model returned empty summary"):
            transformer.transform("input text")

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_llm_exception(self, mock_default_llm):
        """Test transform handles LLM exceptions properly."""
        mock_llm = Mock()
        mock_llm.completion.side_effect = Exception("LLM API error")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        with pytest.raises(
            TransformerError,
            match="Failed to summarize content with fast model: LLM API error",
        ):
            transformer.transform("input text")

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_transform_malformed_response(self, mock_default_llm):
        """Test transform handles malformed LLM response."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.choices = []  # Empty choices
        mock_llm.completion.return_value = mock_response
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        with pytest.raises(
            TransformerError, match="Failed to summarize content with fast model"
        ):
            transformer.transform("input text")

    def test_default_prompt_content(self):
        """Test that default prompt contains expected guidelines."""
        transformer = LLMSummarizeTransformer()

        # Check key elements of the default prompt
        assert "attention or immediate action" in transformer.DEFAULT_PROMPT
        assert "Group similar entries" in transformer.DEFAULT_PROMPT
        assert (
            "outliers, errors, and non-standard patterns" in transformer.DEFAULT_PROMPT
        )
        assert "exact keywords" in transformer.DEFAULT_PROMPT

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_prompt_construction(self, mock_default_llm):
        """Test that prompts are properly constructed with content."""
        mock_llm = self.create_mock_llm("test response")
        mock_default_llm.return_value = mock_llm
        custom_prompt = "Custom summarization prompt"
        transformer = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", prompt=custom_prompt
        )

        input_text = "Test content"
        transformer.transform(input_text)

        # Verify prompt structure
        call_args = mock_llm.completion.call_args[0][0]
        full_prompt = call_args[0]["content"]

        assert full_prompt.startswith(custom_prompt)
        assert "Content to summarize:" in full_prompt
        assert full_prompt.endswith(input_text)


class TestLLMSummarizeTransformerIntegration:
    """Integration tests for LLMSummarizeTransformer."""

    def create_mock_llm(self, response_content: str = "Summarized content"):
        """Create a mock LLM that returns the specified response."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_choice = Mock()
        mock_message = Mock()

        # Properly configure the mock message.content to return a string that supports .strip()
        mock_message.content = response_content
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        mock_llm.completion.return_value = mock_response

        return mock_llm

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_end_to_end_summarization_workflow(self, mock_default_llm):
        """Test complete summarization workflow."""
        mock_llm = self.create_mock_llm(
            "Summarized: kubectl shows 3 healthy pods, 1 failing"
        )
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", input_threshold=100
        )

        # Simulate large kubectl output
        large_output = """
NAME                                READY   STATUS    RESTARTS   AGE
webapp-deployment-abc123-def456     1/1     Running   0          2d
webapp-deployment-abc123-ghi789     1/1     Running   0          2d
webapp-deployment-abc123-jkl012     1/1     Running   0          2d
database-deployment-mno345-pqr678   0/1     Error     3          1d
service/webapp-service              ClusterIP   10.0.1.100   <none>        80/TCP    2d
service/database-service            ClusterIP   10.0.1.101   <none>        5432/TCP  2d
        """.strip()

        # Should apply due to length
        assert transformer.should_apply(large_output)

        # Transform and verify
        result = transformer.transform(large_output)
        assert result == "Summarized: kubectl shows 3 healthy pods, 1 failing"

        # Verify LLM was called with full output
        mock_llm.completion.assert_called_once()
        call_args = mock_llm.completion.call_args[0][0]
        assert large_output in call_args[0]["content"]

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_conditional_application_workflow(self, mock_default_llm):
        """Test that transformation is conditionally applied based on threshold."""
        mock_llm = self.create_mock_llm("Should not be called")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", input_threshold=1000
        )

        # Small output that shouldn't be summarized
        small_output = "pod/webapp-123 is running"

        # Should not apply
        assert not transformer.should_apply(small_output)

        # Large output that should be summarized
        large_output = "a" * 1001
        assert transformer.should_apply(large_output)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_error_handling_workflow(self, mock_default_llm):
        """Test error handling in real-world scenarios."""
        # Test with failing LLM
        mock_llm = Mock()
        mock_llm.completion.side_effect = ConnectionError("Network timeout")
        mock_default_llm.return_value = mock_llm
        transformer = LLMSummarizeTransformer(fast_model="gpt-4o-mini")

        with pytest.raises(TransformerError) as exc_info:
            transformer.transform("input to summarize")

        # Verify error is properly wrapped and chained
        assert "Failed to summarize content with fast model" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ConnectionError)
        assert "Network timeout" in str(exc_info.value.__cause__)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_different_threshold_scenarios(self, mock_default_llm):
        """Test various threshold scenarios."""
        mock_llm = self.create_mock_llm("summary")

        test_cases = [
            (0, "x", True),  # Zero threshold, any input > 0 applies
            (1, "", False),  # Empty input, any positive threshold
            (5, "short", False),  # Input exactly at threshold (not >)
            (5, "longer", True),  # Input above threshold
            (1000, "x" * 999, False),  # Just below default
            (1000, "x" * 1000, False),  # Exactly at threshold
            (1000, "x" * 1001, True),  # Above threshold
        ]

        for threshold, input_text, expected in test_cases:
            mock_default_llm.return_value = mock_llm
            transformer = LLMSummarizeTransformer(
                fast_model="gpt-4o-mini", input_threshold=threshold
            )

            result = transformer.should_apply(input_text)
            assert (
                result == expected
            ), f"Failed for threshold={threshold}, input_len={len(input_text)}"

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_multiple_transformer_instances(self, mock_default_llm):
        """Test using multiple transformer instances with different configs."""
        mock_llm1 = self.create_mock_llm("Summary 1")
        mock_llm2 = self.create_mock_llm("Summary 2")

        def mock_llm_side_effect(model, api_key):
            if model == "gpt-4o-mini":
                return mock_llm1
            elif model == "gpt-3.5-turbo":
                return mock_llm2
            return Mock()

        mock_default_llm.side_effect = mock_llm_side_effect

        transformer1 = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini",
            input_threshold=10,
            prompt="Brief summary:",
        )

        transformer2 = LLMSummarizeTransformer(
            fast_model="gpt-3.5-turbo",
            input_threshold=20,
            prompt="Detailed summary:",
        )

        test_input = "This is test input content"

        # Both should apply (length > both thresholds)
        assert transformer1.should_apply(test_input)
        assert transformer2.should_apply(test_input)

        # Transform with both
        result1 = transformer1.transform(test_input)
        result2 = transformer2.transform(test_input)

        assert result1 == "Summary 1"
        assert result2 == "Summary 2"

        # Verify each used their own LLM and prompt
        mock_llm1.completion.assert_called_once()
        mock_llm2.completion.assert_called_once()

        call1 = mock_llm1.completion.call_args[0][0][0]["content"]
        call2 = mock_llm2.completion.call_args[0][0][0]["content"]

        assert "Brief summary:" in call1
        assert "Detailed summary:" in call2

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_non_expanding_behavior_integration(self, mock_default_llm):
        """Test that transformer may produce longer output and that expansion behavior is handled.

        This test verifies that the transformer itself can produce summaries that are longer
        than the original input. The non-expansion/reversion logic (preventing output that
        is larger than input) is implemented at the tools.py level, not in the transformer.
        """
        # Test case 1: Summary that expands the original data
        expanding_summary = "This is a very long and detailed expansion of the original short input that makes the content much larger than it was before processing with lots of extra words"
        mock_llm_expanding = self.create_mock_llm(expanding_summary)
        mock_default_llm.return_value = mock_llm_expanding

        transformer = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", input_threshold=10
        )

        original_input = "Short input data"

        # Verify that this would normally apply
        assert transformer.should_apply(original_input)

        # When used in the transformation pipeline, the non-expanding logic
        # is handled at the tools.py level, not in the transformer itself.
        # This test verifies the transformer can produce larger output
        result = transformer.transform(original_input)
        assert result == expanding_summary
        assert len(result) > len(original_input)

        # Test case 2: Summary that properly reduces size
        reducing_summary = "Short"
        mock_llm_reducing = self.create_mock_llm(reducing_summary)
        mock_default_llm.return_value = mock_llm_reducing

        transformer2 = LLMSummarizeTransformer(
            fast_model="gpt-4o-mini", input_threshold=10
        )

        longer_input = "This is a much longer input that should be summarized into something shorter"

        assert transformer2.should_apply(longer_input)
        result2 = transformer2.transform(longer_input)
        assert result2 == reducing_summary
        assert len(result2) < len(longer_input)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_integration_with_tools_non_expanding_logic(self, mock_default_llm):
        """Test integration with the tools.py non-expanding logic."""
        from holmes.core.tools import (
            StructuredToolResult,
            StructuredToolResultStatus,
            Tool,
        )
        from holmes.core.transformers import Transformer

        # Create a mock expanding transformer response
        expanding_response = "This response is much much much longer than the original input and would not be useful as a summary since it expands rather than contracts the content size making it counterproductive"
        mock_llm = self.create_mock_llm(expanding_response)
        mock_default_llm.return_value = mock_llm

        # Create a concrete tool class for testing
        class TestTool(Tool):
            def _invoke(self, params, context: ToolInvokeContext):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Original short data",
                )

            def get_parameterized_one_liner(self, params):
                return "test command"

        # Create tool with llm_summarize transformer
        tool = TestTool(
            name="test_tool",
            description="Test tool",
            transformers=[
                Transformer(
                    name="llm_summarize",
                    config={"input_threshold": 5, "fast_model": "fast"},
                )
            ],
        )

        # Invoke the tool - this should trigger the non-expanding logic in tools.py
        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # The result should be the original data, not the expanded summary
        # because tools.py should revert when llm_summarize expands the content
        assert result.data == "Original short data"
        assert len(result.data) < len(expanding_response)

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_non_expanding_logic_with_debug_logging(self, mock_default_llm):
        """Test that explicitly verifies the non-expanding logic is triggered."""
        from holmes.core.tools import (
            StructuredToolResult,
            StructuredToolResultStatus,
            Tool,
        )
        from holmes.core.transformers import Transformer

        # Create a mock expanding transformer response that's much larger than original
        original_data = "This is some original data that is long enough to trigger the llm_summarize transformer because it exceeds the input threshold that we set to a low value so the transformer will definitely apply to this input text"  # Long enough to trigger threshold
        expanding_response = "This is an extremely long and detailed expansion of the original input that goes on and on with lots of additional unnecessary details that make it much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much longer than the original data, completely defeating the purpose of summarization and making the output counterproductive and should be reverted automatically"  # Much longer

        mock_llm = self.create_mock_llm(expanding_response)
        mock_default_llm.return_value = mock_llm

        # Create a concrete tool class for testing
        class TestTool(Tool):
            def _invoke(self, params, context: ToolInvokeContext):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=original_data,  # This will be the input to the transformer
                )

            def get_parameterized_one_liner(self, params):
                return "test command"

        # Create tool with llm_summarize transformer (low threshold so it applies)
        # Need to ensure fast_model is configured AND global_fast_model fallback
        tool = TestTool(
            name="test_tool",
            description="Test tool",
            transformers=[
                Transformer(
                    name="llm_summarize",
                    config={
                        "input_threshold": 50,  # Much lower than our input length
                        "fast_model": "gpt-4o-mini",  # Provide fast_model
                    },
                )
            ],
        )

        # Patch the logger to capture debug messages
        with patch("holmes.core.tools.logger.debug") as mock_debug_logger:
            # Invoke the tool - this should trigger the non-expanding logic
            context = create_mock_tool_invoke_context()
            result = tool.invoke({}, context)

            # Verify that the result is the original data (not the expanded response)
            assert result.data == original_data
            assert len(result.data) < len(expanding_response)

            # Most importantly: Check that the debug log for reversion was called
            debug_calls = mock_debug_logger.call_args_list
            reversion_logged = any(
                "reverted" in str(call) and "llm_summarize" in str(call)
                for call in debug_calls
            )
            assert (
                reversion_logged
            ), f"Expected debug log about reversion, got: {debug_calls}"

            # Also verify the LLM was actually called (so we know the transformer ran)
            mock_llm.completion.assert_called_once()

    @patch("holmes.core.transformers.llm_summarize.DefaultLLM")
    def test_non_expanding_vs_successful_summarization_comparison(
        self, mock_default_llm
    ):
        """Test comparing expanding vs reducing scenarios to ensure logic works correctly."""
        from holmes.core.tools import (
            StructuredToolResult,
            StructuredToolResultStatus,
            Tool,
        )
        from holmes.core.transformers import Transformer

        original_data = "This is some longer original data that should be summarized by the transformer if it works properly and produces a smaller result"

        # Create a concrete tool class for testing
        class TestTool(Tool):
            def _invoke(self, params, context: ToolInvokeContext):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS, data=original_data
                )

            def get_parameterized_one_liner(self, params):
                return "test command"

        # Test Case 1: Expanding response (should be reverted)
        expanding_response = "This is an extremely long and detailed expansion of the original input that goes on and on and on and on and on and on and on and on and on and on and on and on and on and on and on and on and on and on and on and on with lots of additional unnecessary details that make it much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much much longer than the original data, completely defeating the purpose of summarization and making the output counterproductive"
        mock_llm_expanding = self.create_mock_llm(expanding_response)

        # Set up mock BEFORE creating the tool
        mock_default_llm.return_value = mock_llm_expanding

        tool1 = TestTool(
            name="expanding_tool",
            description="Test tool with expanding transformer",
            transformers=[
                Transformer(
                    name="llm_summarize",
                    config={"input_threshold": 50, "fast_model": "gpt-4o-mini"},
                )
            ],
        )

        with patch("holmes.core.tools.logger.debug") as mock_debug1:
            context = create_mock_tool_invoke_context()
            result1 = tool1.invoke({}, context)

            # Should revert to original data
            assert result1.data == original_data
            assert len(result1.data) < len(expanding_response)

            # Should log reversion
            reversion_logged = any(
                "reverted" in str(call) and "llm_summarize" in str(call)
                for call in mock_debug1.call_args_list
            )
            assert (
                reversion_logged
            ), f"Expected reversion log, got: {mock_debug1.call_args_list}"

        # Test Case 2: Reducing response (should be applied)
        reducing_response = "Short summary"
        mock_llm_reducing = self.create_mock_llm(reducing_response)

        # Set up mock BEFORE creating the second tool
        mock_default_llm.return_value = mock_llm_reducing

        tool2 = TestTool(
            name="reducing_tool",
            description="Test tool with reducing transformer",
            transformers=[
                Transformer(
                    name="llm_summarize",
                    config={"input_threshold": 50, "fast_model": "gpt-4o-mini"},
                )
            ],
        )

        with patch("holmes.core.tools.logger.info") as mock_info:
            result2 = tool2.invoke({}, context)

            # Should use the summarized data
            assert result2.data == reducing_response
            assert len(result2.data) < len(original_data)

            # Should log successful transformation
            transformation_logged = any(
                "Applied transformer 'llm_summarize'" in str(call)
                for call in mock_info.call_args_list
            )
            assert (
                transformation_logged
            ), f"Expected transformation log, got: {mock_info.call_args_list}"
