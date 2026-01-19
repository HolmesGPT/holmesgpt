"""
Unit tests for Phase 2.1: Tool transformer integration and validation.
"""

from typing import Dict
from unittest.mock import Mock, patch

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolsetYamlFromConfig,
    YAMLTool,
    YAMLToolset,
)
from holmes.core.transformers import (
    Transformer,
    TransformerError,
    registry,
)
from holmes.core.transformers.base import BaseTransformer
from tests.conftest import create_mock_tool_invoke_context


class MockTransformer(BaseTransformer):
    """Mock transformer for testing."""

    def transform(self, input_text: str) -> str:
        return f"mock_transformed: {input_text}"

    def should_apply(self, input_text: str) -> bool:
        return len(input_text) > 5

    @property
    def name(self) -> str:
        return "mock_transformer"


class TestToolTransformField:
    """Test that Tool and YAMLTool accept transformers field."""

    def test_tool_with_transforms(self):
        """Test that Tool accepts transformers field."""
        transformers = [
            Transformer(name="llm_summarize", config={"input_threshold": 500})
        ]

        # Create a concrete tool for testing
        class ConcreteTestTool(Tool):
            def _invoke(self, params: Dict, context: ToolInvokeContext) -> Mock:
                return Mock()

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool",
            description="Test tool with transformers",
            transformers=transformers,
        )

        assert tool.transformers == transformers

    def test_yaml_tool_with_transforms(self):
        """Test that YAMLTool accepts transformers field."""
        transformers = [
            Transformer(name="llm_summarize", config={"input_threshold": 1000})
        ]

        tool = YAMLTool(
            name="test_yaml_tool",
            description="Test YAML tool with transformers",
            command="echo 'test'",
            transformers=transformers,
        )

        assert tool.transformers == transformers

    def test_tool_without_transforms(self):
        """Test that tools work without transformers field."""

        class ConcreteTestTool(Tool):
            def _invoke(self, params: Dict, context: ToolInvokeContext) -> Mock:
                return Mock()

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool without transformers"
        )

        assert tool.transformers is None


class TestToolsetTransformers:
    """Test that Toolset classes accept transformers parameter."""

    def test_toolset_with_transformers(self):
        """Test that Toolset accepts transformers field."""
        transformers = [
            Transformer(name="llm_summarize", config={"input_threshold": 800})
        ]

        toolset = YAMLToolset(
            name="test_toolset",
            description="Test toolset with transformers",
            transformers=transformers,
            tools=[],
        )

        assert toolset.transformers == transformers

    def test_toolset_yaml_from_config_with_transformers(self):
        """Test that ToolsetYamlFromConfig accepts transformers."""
        transformers = [
            Transformer(name="llm_summarize", config={"input_threshold": 1200})
        ]

        toolset = ToolsetYamlFromConfig(
            name="test_config_toolset", transformers=transformers
        )

        assert toolset.transformers == transformers

    def test_toolset_transformers_propagation(self):
        """Test that toolset transformers propagate to tools that don't have their own."""
        toolset_transformers = [
            Transformer(name="llm_summarize", config={"input_threshold": 600})
        ]

        # Create toolset with transformers
        toolset_data = {
            "name": "test_toolset",
            "description": "Test toolset",
            "transformers": toolset_transformers,
            "tools": [
                {
                    "name": "tool_without_transforms",
                    "description": "Tool without its own transforms",
                    "command": "echo 'test1'",
                },
                {
                    "name": "tool_with_transforms",
                    "description": "Tool with its own transforms",
                    "command": "echo 'test2'",
                    "transformers": [
                        Transformer(
                            name="llm_summarize", config={"input_threshold": 300}
                        )
                    ],
                },
            ],
        }

        toolset = YAMLToolset(**toolset_data)

        # Tool without transforms should inherit from toolset
        assert toolset.tools[0].transformers == toolset_transformers

        # Tool with transforms should have merged configs (toolset + tool)
        tool_with_transforms = toolset.tools[1]
        assert tool_with_transforms.transformers is not None
        assert len(tool_with_transforms.transformers) == 1
        # The tool should get the merged config (override takes precedence)
        assert tool_with_transforms.transformers[0].config["input_threshold"] == 300


class TestToolValidationIntegration:
    """Test transformer validation integration in Tool classes."""

    def setup_method(self):
        """Set up test fixtures."""
        registry.register(MockTransformer)

    def teardown_method(self):
        """Clean up test fixtures."""
        if registry.is_registered("mock_transformer"):
            registry.unregister("mock_transformer")

    def test_tool_validation_success(self):
        """Test tool creation succeeds with valid transforms."""
        transforms = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(self, params: Dict, context: ToolInvokeContext) -> Mock:
                return Mock()

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transforms
        )

        assert tool.transformers == transforms

    def test_tool_validation_clears_invalid_transforms(self):
        """Test tool creation with invalid transformers logs warning but continues."""
        # Note: Tool.__init__ validates transformers during instantiation
        # Invalid transformers will cause validation to fail during model creation

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        # Test creating tool with invalid transformers logs warnings but continues
        invalid_transforms = [
            Transformer(name="nonexistent_transformer", config={}),
            Transformer(name="mock_transformer", config={}),  # This one should succeed
        ]

        with patch("holmes.core.tools.logger.warning") as mock_logger_warning:
            tool = ConcreteTestTool(
                name="test_tool",
                description="Test tool",
                transformers=invalid_transforms,
            )

            # Verify that warning was logged for invalid transformer
            mock_logger_warning.assert_called()
            warning_calls = [
                call
                for call in mock_logger_warning.call_args_list
                if "nonexistent_transformer" in str(call)
            ]
            assert len(warning_calls) > 0

        # Tool should still work despite invalid transformer
        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_yaml_tool_validation(self):
        """Test YAMLTool transformer validation."""
        transforms = [Transformer(name="mock_transformer", config={})]

        tool = YAMLTool(
            name="test_yaml_tool",
            description="Test YAML tool",
            command="echo 'test'",
            transformers=transforms,
        )

        assert tool.transformers == transforms


class TestBackwardCompatibility:
    """Test that existing tools continue to work without transformers."""

    def test_existing_yaml_tool_compatibility(self):
        """Test that existing YAML tools work without transforms."""
        tool = YAMLTool(
            name="legacy_tool",
            description="Legacy tool without transforms",
            command="echo 'legacy'",
        )

        assert tool.transformers is None
        assert tool.name == "legacy_tool"

    def test_existing_toolset_compatibility(self):
        """Test that existing toolsets work without transformers."""
        toolset = YAMLToolset(
            name="legacy_toolset", description="Legacy toolset", tools=[]
        )

        assert toolset.transformers is None
        assert toolset.name == "legacy_toolset"

    def test_mixed_old_new_tools(self):
        """Test toolset with mix of tools with and without transforms."""
        toolset_data = {
            "name": "mixed_toolset",
            "description": "Mixed toolset",
            "tools": [
                {
                    "name": "legacy_tool",
                    "description": "Legacy tool",
                    "command": "echo 'legacy'",
                },
                {
                    "name": "new_tool",
                    "description": "New tool with transforms",
                    "command": "echo 'new'",
                    "transformers": [Transformer(name="llm_summarize", config={})],
                },
            ],
        }

        toolset = YAMLToolset(**toolset_data)

        assert toolset.tools[0].transformers is None
        assert toolset.tools[1].transformers == [
            Transformer(name="llm_summarize", config={})
        ]


class TestToolExecutionPipeline:
    """Test Tool Execution Pipeline with transformers."""

    def setup_method(self):
        """Set up test fixtures."""
        registry.register(MockTransformer)

    def teardown_method(self):
        """Clean up test fixtures."""
        if registry.is_registered("mock_transformer"):
            registry.unregister("mock_transformer")

    def test_successful_tool_execution_with_transformers(self):
        """Test that tools execute successfully and apply transformers."""
        transforms = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="This is a long test output that should be transformed",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transforms
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should have applied transformation
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None
        assert "mock_transformed:" in result.data
        assert "This is a long test output that should be transformed" in result.data

    def test_tool_execution_skips_transformers_on_error(self):
        """Test that transformers are not applied when tool execution fails."""
        transforms = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Tool execution failed",
                    data="Some error output",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transforms
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should not have applied transformation due to error status
        assert result.status == StructuredToolResultStatus.ERROR
        assert "mock_transformed:" not in str(result.data)
        assert result.data == "Some error output"

    def test_tool_execution_skips_transformers_on_empty_data(self):
        """Test that transformers are not applied when data is empty."""
        transforms = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA, data=""
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transforms
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should not have applied transformation due to empty data
        assert result.status == StructuredToolResultStatus.NO_DATA
        assert result.data == ""

    def test_transformer_failure_handling(self):
        """Test that transformer failures are handled gracefully."""

        # Create a transformer that will fail
        class FailingTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                raise TransformerError("Transformer failed")

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "failing_transformer"

        registry.register(FailingTransformer)

        try:
            transforms = [Transformer(name="failing_transformer", config={})]

            class ConcreteTestTool(Tool):
                def _invoke(
                    self, params: Dict, context: ToolInvokeContext
                ) -> StructuredToolResult:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS, data="Test output"
                    )

                def get_parameterized_one_liner(self, params: Dict) -> str:
                    return "test command"

            tool = ConcreteTestTool(
                name="test_tool",
                description="Test tool",
                transformers=transforms,
            )

            with patch("holmes.core.tools.logger") as mock_logging:
                context = create_mock_tool_invoke_context()
                result = tool.invoke({}, context)

                # Should return original data when transformer fails
                assert result.status == StructuredToolResultStatus.SUCCESS
                assert result.data == "Test output"

                # Should log warning about transformer failure
                mock_logging.warning.assert_called()
                warning_call = mock_logging.warning.call_args[0][0]
                assert "failing_transformer" in warning_call
                assert "failed" in warning_call

        finally:
            if registry.is_registered("failing_transformer"):
                registry.unregister("failing_transformer")

    def test_multiple_transformers_chaining(self):
        """Test that multiple transformers are applied in sequence."""

        class SecondTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                return f"second_transformed: {input_text}"

            def should_apply(self, input_text: str) -> bool:
                return True

            @property
            def name(self) -> str:
                return "second_transformer"

        registry.register(SecondTransformer)

        try:
            transforms = [
                Transformer(name="mock_transformer", config={}),
                Transformer(name="second_transformer", config={}),
            ]

            class ConcreteTestTool(Tool):
                def _invoke(
                    self, params: Dict, context: ToolInvokeContext
                ) -> StructuredToolResult:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS,
                        data="Original text that should be transformed twice",
                    )

                def get_parameterized_one_liner(self, params: Dict) -> str:
                    return "test command"

            tool = ConcreteTestTool(
                name="test_tool",
                description="Test tool",
                transformers=transforms,
            )

            context = create_mock_tool_invoke_context()
            result = tool.invoke({}, context)

            # Should have applied both transformations in sequence
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data is not None
            assert "second_transformed:" in result.data
            assert "mock_transformed:" in result.data

        finally:
            if registry.is_registered("second_transformer"):
                registry.unregister("second_transformer")

    def test_transformer_conditional_application(self):
        """Test that transformers are only applied when should_apply returns True."""

        class ConditionalTransformer(BaseTransformer):
            def transform(self, input_text: str) -> str:
                return f"conditional_transformed: {input_text}"

            def should_apply(self, input_text: str) -> bool:
                # Only apply to inputs longer than 10 characters
                return len(input_text) > 10

            @property
            def name(self) -> str:
                return "conditional_transformer"

        registry.register(ConditionalTransformer)

        try:
            transforms = [Transformer(name="conditional_transformer", config={})]

            class ConcreteTestTool(Tool):
                def _invoke(
                    self, params: Dict, context: ToolInvokeContext
                ) -> StructuredToolResult:
                    data = params.get("data", "short")
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS, data=data
                    )

                def get_parameterized_one_liner(self, params: Dict) -> str:
                    return "test command"

            tool = ConcreteTestTool(
                name="test_tool",
                description="Test tool",
                transformers=transforms,
            )

            # Test with short input - should not transform
            context = create_mock_tool_invoke_context()
            result = tool.invoke({"data": "short"}, context)
            assert result.data == "short"
            assert (
                result.data is not None
                and "conditional_transformed:" not in result.data
            )

            # Test with long input - should transform
            result = tool.invoke(
                {"data": "this is a very long input that should be transformed"},
                context,
            )
            assert result.data is not None
            assert "conditional_transformed:" in result.data

        finally:
            if registry.is_registered("conditional_transformer"):
                registry.unregister("conditional_transformer")

    def test_transformer_preserves_original_result_structure(self):
        """Test that transformer only modifies data field, preserving other result fields."""
        transforms = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output to transform",
                    url="http://example.com",
                    invocation="test command",
                    params={"test": "param"},
                    return_code=0,
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transforms
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Data should be transformed
        assert result.data is not None
        assert "mock_transformed:" in result.data

        # Other fields should be preserved
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.url == "http://example.com"
        assert result.invocation == "test command"
        assert result.params == {"test": "param"}
        assert result.return_code == 0

    def test_performance_metrics_logging(self):
        """Test that transformer performance metrics are logged."""
        transforms = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output that will be transformed for performance measurement",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transforms
        )

        with patch("holmes.core.tools.logger") as mock_logging:
            context = create_mock_tool_invoke_context()
            tool.invoke({}, context)

            # Should log transformer application with performance metrics
            info_calls = [call[0][0] for call in mock_logging.info.call_args_list]
            transformer_log = next(
                (call for call in info_calls if "Applied transformer" in call), None
            )

            assert transformer_log is not None
            assert "mock_transformer" in transformer_log
            assert "test_tool" in transformer_log
            assert "size:" in transformer_log
            assert "chars" in transformer_log

    def test_tool_without_transformers_unchanged(self):
        """Test that tools without transformers work exactly as before."""

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output without transformation",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool",
            description="Test tool",
            # No transformers
        )

        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)

        # Should return unchanged result
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Test output without transformation"


class TestTransformerCachingOptimization:
    """Test the transformer caching optimization behavior."""

    def setup_method(self):
        """Set up test fixtures."""
        # Register mock transformer for tests
        if not registry.is_registered("mock_transformer"):
            registry.register(MockTransformer)

    def teardown_method(self):
        """Clean up test fixtures."""
        # Unregister mock transformer
        if registry.is_registered("mock_transformer"):
            registry.unregister("mock_transformer")

    def test_transformer_instances_cached_during_initialization(self):
        """Test that transformer instances are created once during tool initialization and cached."""
        transformers = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output for transformation",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        # Create tool - this should trigger model_post_init and cache transformers
        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transformers
        )

        # Verify that transformer instances are cached
        assert tool._transformer_instances is not None
        assert len(tool._transformer_instances) == 1
        assert tool._transformer_instances[0].name == "mock_transformer"

        # Verify the cached instance is a real transformer, not a config
        cached_transformer = tool._transformer_instances[0]
        assert hasattr(cached_transformer, "transform")
        assert hasattr(cached_transformer, "should_apply")

    def test_transformer_instances_reused_across_multiple_calls(self):
        """Test that the same transformer instances are reused across multiple tool invocations."""
        transformers = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output for transformation",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transformers
        )

        # Get reference to cached transformer instance
        initial_transformer = tool._transformer_instances[0]
        initial_id = id(initial_transformer)

        # Invoke the tool multiple times
        context = create_mock_tool_invoke_context()
        result1 = tool.invoke({}, context)
        result2 = tool.invoke({}, context)
        result3 = tool.invoke({}, context)

        # Verify that the same transformer instance is still cached
        assert tool._transformer_instances[0] is initial_transformer
        assert id(tool._transformer_instances[0]) == initial_id

        # Verify transformations worked correctly
        assert "mock_transformed:" in result1.data
        assert "mock_transformed:" in result2.data
        assert "mock_transformed:" in result3.data

    def test_model_post_init_with_no_transformers(self):
        """Test that model_post_init handles tools with no transformers correctly."""

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output without transformers",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        # Create tool without transformers
        tool = ConcreteTestTool(
            name="test_tool",
            description="Test tool",
            # No transformers
        )

        # Verify that _transformer_instances is None
        assert tool._transformer_instances is None

        # Verify tool still works correctly
        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Test output without transformers"

    def test_transformer_initialization_failure_handling(self):
        """Test graceful handling when transformer initialization fails during model_post_init."""

        # Create a transformer config that will fail to initialize
        transformers = [
            Transformer(name="nonexistent_transformer", config={}),
            Transformer(name="mock_transformer", config={}),  # This one should succeed
        ]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output for transformation",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        # Create tool - initialization should handle the failure gracefully
        with patch("holmes.core.tools.logger.warning") as mock_logger_warning:
            tool = ConcreteTestTool(
                name="test_tool", description="Test tool", transformers=transformers
            )

            # Verify that warning was logged for failed transformer
            mock_logger_warning.assert_called()
            warning_calls = [
                call
                for call in mock_logger_warning.call_args_list
                if "nonexistent_transformer" in str(call)
            ]
            assert len(warning_calls) > 0

        # Verify that only the successful transformer was cached
        assert tool._transformer_instances is not None
        assert len(tool._transformer_instances) == 1
        assert tool._transformer_instances[0].name == "mock_transformer"

        # Verify tool still works with the successful transformer
        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "mock_transformed:" in result.data

    def test_transformer_initialization_empty_transformers_list(self):
        """Test that model_post_init handles empty transformers list correctly."""
        transformers = []

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transformers
        )

        # Empty transformers list should result in None (no transformer processing needed)
        assert tool._transformer_instances is None

        # Tool should still work correctly
        context = create_mock_tool_invoke_context()
        result = tool.invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Test output"

    def test_performance_optimization_prevents_recreation(self):
        """Test that transformers are not recreated on each tool call."""
        transformers = [Transformer(name="mock_transformer", config={})]

        class ConcreteTestTool(Tool):
            def _invoke(
                self, params: Dict, context: ToolInvokeContext
            ) -> StructuredToolResult:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="Test output for transformation",
                )

            def get_parameterized_one_liner(self, params: Dict) -> str:
                return "test command"

        tool = ConcreteTestTool(
            name="test_tool", description="Test tool", transformers=transformers
        )

        # Mock the registry.create_transformer to track calls
        with patch.object(
            registry, "create_transformer", wraps=registry.create_transformer
        ) as mock_create:
            # Invoke the tool multiple times
            context = create_mock_tool_invoke_context()
            tool.invoke({}, context)
            tool.invoke({}, context)
            tool.invoke({}, context)

            # create_transformer should NOT be called during tool invocation
            # It should only be called during initialization (which happened before this patch)
            mock_create.assert_not_called()
