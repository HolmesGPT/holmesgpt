# type: ignore
import os
import tempfile
from unittest.mock import Mock, patch

import pytest
import yaml

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolsetStatusEnum,
)
from tests.conftest import create_mock_tool_invoke_context
from tests.llm.utils.mock_toolset import (
    MockableToolWrapper,
    MockFileManager,
    MockMode,
    MockToolsetManager,
    sanitize_filename,
)


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "input_filename,expected",
        [
            # Basic cases
            ("normal_filename.txt", "normal_filename.txt"),
            ("simple-name", "simple-name"),
            ("123_abc", "123_abc"),
            # URL scheme removal
            ("http://example.com/file.txt", "example.com_file.txt"),
            ("https://example.com/file.txt", "example.com_file.txt"),
            ("HTTP://EXAMPLE.COM/FILE.TXT", "EXAMPLE.COM_FILE.TXT"),
            ("HTTPS://EXAMPLE.COM/FILE.TXT", "EXAMPLE.COM_FILE.TXT"),
            # URL decoding
            ("file%20name.txt", "file_name.txt"),
            ("path%2Fto%2Ffile.txt", "path_to_file.txt"),
            ("query%3Fparam%3Dvalue", "query_param_value"),
            # Datetime
            (
                "statefulset-logs_2025-06-12T05:58:20Z",
                "statefulset-logs_2025-06-12T05_58_20Z",
            ),
            # Special characters replacement
            ("file with spaces.txt", "file_with_spaces.txt"),
            ("file/with/slashes", "file_with_slashes"),
            ("file\\with\\backslashes", "file_with_backslashes"),
            ("file:with:colons", "file_with_colons"),
            ("file*with*stars", "file_with_stars"),
            ("file?with?questions", "file_with_questions"),
            ('file"with"quotes', "file_with_quotes"),
            ("file<with>brackets", "file_with_brackets"),
            ("file|with|pipes", "file_with_pipes"),
            # Multiple consecutive underscores
            ("file___with___multiple___underscores", "file_with_multiple_underscores"),
            ("file____many____underscores", "file_many_underscores"),
            # Leading/trailing underscores and dots
            ("_leading_underscore", "leading_underscore"),
            ("trailing_underscore_", "trailing_underscore"),
            ("__multiple__leading__", "multiple_leading"),
            ("__trailing__multiple__", "trailing_multiple"),
            (".leading.dot", "leading.dot"),
            ("trailing.dot.", "trailing.dot"),
            ("...multiple...dots...", "multiple...dots"),
            # Case conversion
            ("UPPERCASE.TXT", "UPPERCASE.TXT"),
            ("MixedCase.File", "MixedCase.File"),
            # Combined scenarios
            (
                "https://example.com/My%20File%20Name.txt",
                "example.com_My_File_Name.txt",
            ),
            (
                "HTTP://SITE.COM/PATH/TO/FILE%20WITH%20SPACES.PDF",
                "SITE.COM_PATH_TO_FILE_WITH_SPACES.PDF",
            ),
            ("file___with***many!!!special@@@chars", "file_with_many_special_chars"),
            # Edge cases
            ("", ""),
            (".", ""),
            ("_", ""),
            ("__", ""),
            ("...", ""),
            ("___", ""),
            ("_._", ""),
            # Only allowed characters
            ("abc123_-.", "abc123_-"),
            ("test.file-name_123", "test.file-name_123"),
            # Complex URL with query parameters
            (
                "https://api.example.com/v1/users?id=123&name=John%20Doe",
                "api.example.com_v1_users_id_123_name_John_Doe",
            ),
        ],
    )
    def test_sanitize_filename(self, input_filename, expected):
        """Test sanitize_filename with various input scenarios."""
        result = sanitize_filename(input_filename)
        assert result == expected


def assert_toolset_enabled(mock_toolsets: MockToolsetManager, toolset_name: str):
    for toolset in mock_toolsets.toolsets:
        if toolset.name == toolset_name:
            assert (
                toolset.status == ToolsetStatusEnum.ENABLED
            ), f"Expected toolset {toolset_name} to be enabled but it is disabled"
            return
    assert False, f"Expected toolset {toolset_name} to be found in the list of toolsets"


@pytest.mark.skip(
    reason="Test currently broken on github because kubernetes is not available"
)
def test_enabled_toolsets():
    # This test ensures `MockToolsetManager` behaves like HolmesGPT and that it returns the same
    # list of enabled toolsets as HolmesGPT in production
    mock_config = Mock()
    mock_config.mode = MockMode.MOCK
    mock_config.regenerate_all_mocks = False

    mock_toolsets = MockToolsetManager(
        test_case_folder="../fixtures/test_ask_holmes/01_how_many_pods",
        mock_generation_config=mock_config,
    )
    # These toolsets are expected to be enabled by default
    # If this changes it's ok to update the list below
    assert_toolset_enabled(mock_toolsets, "kubernetes/core")
    assert_toolset_enabled(mock_toolsets, "kubernetes/logs")
    assert_toolset_enabled(mock_toolsets, "internet")


class TestMockFileManager:
    def test_mock_file_path_generation(self):
        """Test that mock file paths are generated correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MockFileManager(tmpdir, add_params_to_filename=True)

            path = manager._get_mock_file_path(
                "test_tool", {"param1": "value1", "param2": "value2"}
            )
            assert path.endswith(
                "test_toolvalue1_value2.txt"
            )  # Note: sanitize_filename removes underscores

            # Test without params in filename
            manager = MockFileManager(tmpdir, add_params_to_filename=False)
            path = manager._get_mock_file_path("test_tool", {"param1": "value1"})
            assert path.endswith("test_tool.txt")

    def test_write_and_read_mock(self):
        """Test writing and reading mock data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MockFileManager(tmpdir)

            # Create test result
            result = StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data="Test output data",
                metadata={"key": "value"},
            )

            # Write mock
            mock_file = manager.write_mock(
                tool_name="test_tool",
                toolset_name="test_toolset",
                params={"param": "value"},
                result=result,
            )

            assert os.path.exists(mock_file)

            # Read mock back
            mock = manager.read_mock("test_tool", {"param": "value"})
            assert mock is not None
            assert mock.tool_name == "test_tool"
            assert mock.toolset_name == "test_toolset"
            assert mock.return_value.data == "Test output data"
            assert mock.return_value.status == StructuredToolResultStatus.SUCCESS

    def test_read_nonexistent_mock(self):
        """Test reading a mock that doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MockFileManager(tmpdir)
            mock = manager.read_mock("nonexistent", {})
            assert mock is None

    def test_clear_mocks(self):
        """Test clearing mock files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MockFileManager(tmpdir)

            # Create some mock files
            with open(os.path.join(tmpdir, "mock1.txt"), "w") as f:
                f.write("mock1")
            with open(os.path.join(tmpdir, "mock2.json"), "w") as f:
                f.write("{}")

            # Mock request for pytest tracking
            mock_request = Mock()
            mock_request.node.user_properties = []

            # Clear mocks
            cleared = manager.clear_mocks_for_test(mock_request)

            assert len(cleared) == 2
            assert not os.path.exists(os.path.join(tmpdir, "mock1.txt"))
            assert not os.path.exists(os.path.join(tmpdir, "mock2.json"))

            # Check tracking
            assert len(mock_request.node.user_properties) == 1
            assert mock_request.node.user_properties[0][0] == "mocks_cleared"


class TestMockableToolWrapper:
    def create_mock_tool(self):
        """Create a mock tool for testing."""
        tool = Mock(spec=Tool)
        tool.name = "test_tool"
        tool.description = "Test tool"
        tool.parameters = {}
        tool.user_description = None
        tool.invoke = Mock(
            return_value=StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS, data="Real tool output"
            )
        )
        tool.get_parameterized_one_liner = Mock(return_value="test_tool()")
        return tool

    def test_live_mode(self):
        """Test that live mode calls the real tool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = self.create_mock_tool()
            file_manager = MockFileManager(tmpdir)
            mock_request = Mock()

            wrapper = MockableToolWrapper(
                tool=tool,
                mode=MockMode.LIVE,
                file_manager=file_manager,
                toolset_name="test_toolset",
                request=mock_request,
            )

            context = create_mock_tool_invoke_context()
            result = wrapper.invoke({}, context)

            # Should call real tool
            tool.invoke.assert_called_once_with({}, context)
            assert result.data == "Real tool output"

    def test_mock_mode_with_existing_mock(self):
        """Test that mock mode reads from mock files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = self.create_mock_tool()
            file_manager = MockFileManager(tmpdir)
            mock_request = Mock()

            # First, write a mock file
            mock_result = StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS, data="Mocked output"
            )
            file_manager.write_mock(
                tool_name="test_tool",
                toolset_name="test_toolset",
                params={},
                result=mock_result,
            )

            # Create wrapper in mock mode
            wrapper = MockableToolWrapper(
                tool=tool,
                mode=MockMode.MOCK,
                file_manager=file_manager,
                toolset_name="test_toolset",
                request=mock_request,
            )

            context = create_mock_tool_invoke_context()
            result = wrapper.invoke({}, context)

            # Should NOT call real tool
            tool.invoke.assert_not_called()
            assert result.data == "Mocked output"

    def test_mock_mode_without_mock_raises_error(self):
        """Test that mock mode returns error result when no mock exists."""
        context = create_mock_tool_invoke_context()
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = self.create_mock_tool()
            file_manager = MockFileManager(tmpdir)
            mock_request = Mock()
            mock_request.node.user_properties = []

            wrapper = MockableToolWrapper(
                tool=tool,
                mode=MockMode.MOCK,
                file_manager=file_manager,
                toolset_name="test_toolset",
                request=mock_request,
            )

            result = wrapper.invoke({}, context)

            # Should return error result instead of raising
            assert result.status == StructuredToolResultStatus.ERROR
            assert "Mock data error" in result.error
            assert "No mock data found" in result.error

            # Check that error was tracked in user_properties
            assert len(mock_request.node.user_properties) == 1
            assert mock_request.node.user_properties[0][0] == "mock_data_error"
            assert mock_request.node.user_properties[0][1]["tool"] == "test_tool"

    def test_generate_mode(self):
        """Test that generate mode calls tool and saves mock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = self.create_mock_tool()
            file_manager = MockFileManager(tmpdir)
            mock_request = Mock()
            mock_request.node.user_properties = []

            wrapper = MockableToolWrapper(
                tool=tool,
                mode=MockMode.GENERATE,
                file_manager=file_manager,
                toolset_name="test_toolset",
                request=mock_request,
            )

            context = create_mock_tool_invoke_context()
            result = wrapper.invoke({}, context)

            # Should call real tool
            tool.invoke.assert_called_once_with({}, context)
            assert result.data == "Real tool output"

            # Should save mock file
            saved_mock = file_manager.read_mock("test_tool", {})
            assert saved_mock is not None
            assert saved_mock.return_value.data == "Real tool output"

            # Should track generation
            assert len(mock_request.node.user_properties) == 1
            assert mock_request.node.user_properties[0][0] == "generated_mock_file"


class TestToolsetsYamlConfiguration:
    """Test per-test toolsets.yaml configuration loading."""

    def test_load_custom_toolsets_from_yaml(self):
        """Test that MockToolsetManager loads toolsets.yaml from test folder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a toolsets.yaml file with configuration
            toolsets_config = {
                "toolsets": {
                    "kubernetes/core": {"enabled": True},
                    "kubernetes/logs": {"enabled": False},
                    "prometheus/metrics": {
                        "enabled": True,
                        "config": {"prometheus_url": "http://custom-prometheus:9090"},
                    },
                }
            }

            toolsets_path = os.path.join(tmpdir, "toolsets.yaml")
            with open(toolsets_path, "w") as f:
                yaml.dump(toolsets_config, f)

            mock_config = Mock()
            mock_config.mode = MockMode.MOCK
            mock_config.generate_mocks = False
            mock_config.regenerate_all_mocks = False

            mock_request = Mock()
            mock_request.node.user_properties = []

            manager = MockToolsetManager(
                test_case_folder=tmpdir,
                mock_generation_config=mock_config,
                request=mock_request,
            )

            # Verify all builtin toolsets are loaded but our overrides are applied
            enabled_names = [
                t.name
                for t in manager.toolsets
                if t.status == ToolsetStatusEnum.ENABLED
            ]
            all_names = [t.name for t in manager.toolsets]

            # Debug print
            print(f"Enabled toolsets: {enabled_names}")
            print(f"All toolsets: {all_names}")
            print(f"Number of all toolsets: {len(all_names)}")

            # All builtin toolsets should be present
            assert (
                len(all_names) > 3
            ), f"Should have all builtin toolsets loaded, but got only {len(all_names)}: {all_names}"

            # Check that our custom configurations were applied
            # kubernetes/core should be enabled (explicitly set to True)
            assert (
                "kubernetes/core" in enabled_names
            ), "kubernetes/core should be enabled"

            # kubernetes/logs should NOT be enabled (explicitly set to False)
            assert (
                "kubernetes/logs" not in enabled_names
            ), "kubernetes/logs should be disabled"
            assert (
                "kubernetes/logs" in all_names
            ), "kubernetes/logs should still be in all_toolsets"

            # prometheus/metrics should be enabled with custom config
            assert (
                "prometheus/metrics" in enabled_names
            ), "prometheus/metrics should be enabled"

            # Find the prometheus toolset to verify custom config
            prometheus_toolset = next(
                (t for t in manager.toolsets if t.name == "prometheus/metrics"),
                None,
            )
            assert (
                prometheus_toolset is not None
            ), "prometheus/metrics toolset should exist"
            assert (
                prometheus_toolset.config.get("prometheus_url")
                == "http://custom-prometheus:9090"
            ), "prometheus/metrics should have custom config"


class TestMockToolsMatching:
    def test_mock_tools_exact_match(self):
        """Test that mocked tools return the expected data when parameters match exactly."""
        with patch(
            "holmes.plugins.toolsets.service_discovery.find_service_url",
            return_value="http://mock-prometheus:9090",
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                # Set up file-based mock
                file_manager = MockFileManager(tmpdir, add_params_to_filename=True)
                params = {"field1": "1", "field2": "2"}
                mock_result = StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="this tool is mocked",
                    params=params,
                )

                # Write mock file for the exact params
                file_manager.write_mock(
                    tool_name="kubectl_describe",
                    toolset_name="kubernetes/core",
                    params=params,
                    result=mock_result,
                )

                # Create MockToolsetManager in mock mode
                mock_request = Mock()
                mock_request.node.user_properties = []

                # Create a mock config object
                mock_config = Mock()
                mock_config.mode = MockMode.MOCK
                mock_config.generate_mocks = False
                mock_config.regenerate_all_mocks = False

                mock_toolsets = MockToolsetManager(
                    test_case_folder=tmpdir,
                    mock_generation_config=mock_config,
                    request=mock_request,
                )

                # Use tool executor to invoke the tool
                from holmes.core.tools_utils.tool_executor import ToolExecutor

                tool_executor = ToolExecutor(mock_toolsets.toolsets)

                context = create_mock_tool_invoke_context()
                tool = tool_executor.get_tool_by_name("kubectl_describe")
                result = tool.invoke(params, context)

                # Should return mocked data for exact match
                assert result.data == "this tool is mocked"

    @pytest.mark.parametrize(
        "params",
        [
            {"field1": "1", "field2": "2"},
            {"field1": "1", "field2": "2", "field3": "3"},
            {"any": "params"},
        ],
    )
    def test_mock_tools_without_params_in_filename(self, params):
        """Test that mocks work for any params when add_params_to_filename=False."""
        with patch(
            "holmes.plugins.toolsets.service_discovery.find_service_url",
            return_value="http://mock-prometheus:9090",
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                # Set up file-based mock WITHOUT params in filename
                file_manager = MockFileManager(tmpdir, add_params_to_filename=False)
                mock_result = StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="this tool is mocked",
                    params={},  # Will be ignored when matching
                )

                # Write mock file without params in filename
                file_manager.write_mock(
                    tool_name="kubectl_describe",
                    toolset_name="kubernetes/core",
                    params={},  # Params don't matter when add_params_to_filename=False
                    result=mock_result,
                )

                # Create MockToolsetManager in mock mode
                mock_request = Mock()
                mock_request.node.user_properties = []

                # Create a mock config object
                mock_config = Mock()
                mock_config.mode = MockMode.MOCK
                mock_config.generate_mocks = False
                mock_config.regenerate_all_mocks = False

                mock_toolsets = MockToolsetManager(
                    test_case_folder=tmpdir,
                    mock_generation_config=mock_config,
                    request=mock_request,
                )

                # Use tool executor to invoke the tool
                from holmes.core.tools_utils.tool_executor import ToolExecutor

                tool_executor = ToolExecutor(mock_toolsets.toolsets)
                context = create_mock_tool_invoke_context()
                tool = tool_executor.get_tool_by_name("kubectl_describe")
                result = tool.invoke(params, context)

                # Should return mocked data for ANY params when add_params_to_filename=False
                assert result.data == "this tool is mocked"

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"field1": "1"},
            {"field2": "2"},
            {"field1": "1", "field2": "XXX"},
            {"field1": "XXX", "field2": "2"},
            {"field3": "3"},
        ],
    )
    def test_mock_tools_do_not_match(self, params):
        """Test that tools return error result when parameters don't match in mock mode."""
        with patch(
            "holmes.plugins.toolsets.service_discovery.find_service_url",
            return_value="http://mock-prometheus:9090",
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                # Set up file-based mock with specific params
                file_manager = MockFileManager(tmpdir, add_params_to_filename=True)
                mock_result = StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="this tool is mocked",
                    params={"field1": "1", "field2": "2"},
                )

                # Write mock file only for specific params
                file_manager.write_mock(
                    tool_name="kubectl_describe",
                    toolset_name="kubernetes/core",
                    params={"field1": "1", "field2": "2"},
                    result=mock_result,
                )

                # Create MockToolsetManager in mock mode (not generate mode)
                mock_request = Mock()
                mock_request.node.user_properties = []

                # Create a mock config object
                mock_config = Mock()
                mock_config.mode = MockMode.MOCK
                mock_config.generate_mocks = False
                mock_config.regenerate_all_mocks = False

                mock_toolsets = MockToolsetManager(
                    test_case_folder=tmpdir,
                    mock_generation_config=mock_config,
                    request=mock_request,
                )

                # Use tool executor to invoke the tool
                from holmes.core.tools_utils.tool_executor import ToolExecutor

                tool_executor = ToolExecutor(mock_toolsets.toolsets)

                # In mock mode, calling with non-matching params should raise MockDataNotFoundError
                context = create_mock_tool_invoke_context()

                # In mock mode, calling with non-matching params should return error result
                tool = tool_executor.get_tool_by_name("kubectl_describe")
                result = tool.invoke(params, context)

                # Should return error result
                assert result.status == StructuredToolResultStatus.ERROR
                assert "Mock data error" in result.error

                # Check that error was tracked in user_properties
                errors = [
                    p
                    for p in mock_request.node.user_properties
                    if p[0] == "mock_data_error"
                ]
                assert len(errors) > 0
                assert errors[0][1]["tool"] == "kubectl_describe"

    def test_mock_tools_generate_mode_does_not_throw(self):
        """Test that generate mode creates mocks when they don't exist."""
        with patch(
            "holmes.plugins.toolsets.service_discovery.find_service_url",
            return_value="http://mock-prometheus:9090",
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                # Create MockToolsetManager in generate mode
                mock_request = Mock()
                mock_request.node.user_properties = []

                # Create a mock config object
                mock_config = Mock()
                mock_config.mode = MockMode.GENERATE
                mock_config.generate_mocks = True
                mock_config.regenerate_all_mocks = False

                mock_toolsets = MockToolsetManager(
                    test_case_folder=tmpdir,
                    mock_generation_config=mock_config,
                    request=mock_request,
                )

                # Find the kubectl_describe tool and mock its _invoke method
                from holmes.core.tools_utils.tool_executor import ToolExecutor

                tool_executor = ToolExecutor(mock_toolsets.toolsets)
                kubectl_tool = tool_executor.get_tool_by_name("kubectl_describe")

                if kubectl_tool:
                    # Mock the underlying tool's _invoke method
                    original_invoke = kubectl_tool._tool._invoke
                    kubectl_tool._tool._invoke = Mock(
                        return_value=StructuredToolResult(
                            status=StructuredToolResultStatus.SUCCESS,
                            data="Generated output from mock",
                        )
                    )

                    try:
                        # In generate mode, this should not throw even without existing mocks
                        context = create_mock_tool_invoke_context()
                        tool = tool_executor.get_tool_by_name("kubectl_describe")
                        result = tool.invoke({"foo": "bar"}, context)

                        # Should have called the mocked tool and saved the result
                        assert result.status == StructuredToolResultStatus.SUCCESS
                        assert "Generated output" in result.data

                        # Verify mock was generated
                        file_manager = MockFileManager(
                            tmpdir, add_params_to_filename=True
                        )
                        saved_mock = file_manager.read_mock(
                            "kubectl_describe", {"foo": "bar"}
                        )
                        assert saved_mock is not None
                        assert (
                            saved_mock.return_value.data == "Generated output from mock"
                        )
                    finally:
                        # Restore original method
                        if kubectl_tool:
                            kubectl_tool._tool._invoke = original_invoke
                else:
                    # If kubectl_describe is not available, skip test
                    pytest.skip("kubectl_describe tool not available")

    def test_default_toolsets_when_no_yaml_provided(self):
        """Test that default toolsets are loaded when no toolsets.yaml is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create MockToolsetManager without toolsets.yaml file
            mock_request = Mock()
            mock_request.node.user_properties = []

            mock_config = Mock()
            mock_config.mode = MockMode.MOCK
            mock_config.generate_mocks = False
            mock_config.regenerate_all_mocks = False

            mock_toolsets = MockToolsetManager(
                test_case_folder=tmpdir,
                mock_generation_config=mock_config,
                request=mock_request,
            )

            # Verify that default toolsets are loaded
            assert mock_toolsets.toolsets is not None
            assert len(mock_toolsets.toolsets) > 0

            # Check that some common default toolsets are present
            toolset_names = {
                ts.name
                for ts in mock_toolsets.toolsets
                if ts.status == ToolsetStatusEnum.ENABLED
            }

            # These are commonly enabled by default
            expected_default_toolsets = {
                "kubernetes/core",
                "kubernetes/logs",
                "bash",
                "internet",
            }

            # At least some of the expected defaults should be present
            assert len(expected_default_toolsets & toolset_names) > 0

            # Verify toolsets have proper status
            for toolset in mock_toolsets.toolsets:
                assert toolset.status in [
                    ToolsetStatusEnum.ENABLED,
                    ToolsetStatusEnum.DISABLED,
                ]
