# type: ignore
import glob
import json
import logging
import os
import re
import threading
import urllib
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import pytest
from pydantic import BaseModel

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetStatusEnum,
    YAMLTool,
    YAMLToolset,
)
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_file
from tests.llm.utils.mock_dal import load_mock_dal


# Custom exceptions for better error handling
class MockDataError(Exception):
    """Base exception for mock data errors."""

    def __init__(self, message: str, tool_name: Optional[str] = None):
        self.tool_name = tool_name
        if tool_name:
            helpful_message = (
                f"Missing mock data: '{message}'. \n"
                f"To fix this:\n"
                f"1. Run with live tools: RUN_LIVE=true pytest ...\n"
                f"2. Generate missing mocks (keeps existing, may cause inconsistent data): pytest ... --generate-mocks\n"
                f"3. Regenerate ALL mocks (replaces all existing, ensures consistency): pytest ... --regenerate-all-mocks\n\n"
            )
            super().__init__(helpful_message)
        else:
            super().__init__(message)


class MockDataNotFoundError(MockDataError):
    """Raised when mock data file doesn't exist."""

    pass


class MockDataCorruptedError(MockDataError):
    """Raised when mock data file can't be parsed."""

    pass


class MockValidationError(MockDataError):
    """Raised when mock data doesn't match tool signature."""

    pass


class MockMode(Enum):
    """Modes for mock tool execution."""

    MOCK = "mock"  # Use existing mock files
    GENERATE = "generate"  # Generate new mock files and run tools in live mode
    LIVE = "live"  # Use real tools without mocking


class MockPolicy(Enum):
    """Per-test mock policy that can override global settings."""

    ALWAYS_MOCK = "always_mock"  # Force mock mode regardless of global settings
    NEVER_MOCK = "never_mock"  # Force live mode regardless of global settings
    INHERIT = "inherit"  # Use global settings (default)


class MockGenerationConfig:
    def __init__(self, generate_mocks_enabled, regenerate_all_enabled, mock_mode):
        self.generate_mocks = generate_mocks_enabled
        self.regenerate_all_mocks = regenerate_all_enabled
        self.mode = mock_mode


def check_for_mock_errors(request: pytest.FixtureRequest) -> None:
    """Check if any mock errors occurred during the test and raise an exception if so."""
    mock_errors = []
    for prop in request.node.user_properties:
        if isinstance(prop, tuple) and len(prop) >= 2 and prop[0] == "mock_data_error":
            mock_errors.append(prop[1])

    logging.info(f"Checking for mock errors. Found {len(mock_errors)} error(s)")

    if mock_errors:
        # Simple, clean error message for test output
        error_message = f"Test failed due to {len(mock_errors)} mock data error(s)."

        # Raise the appropriate exception type based on the first error
        first_error_type = mock_errors[0].get("type", "MockDataError")
        if first_error_type == "MockDataNotFoundError":
            raise MockDataNotFoundError(error_message)
        elif first_error_type == "MockDataCorruptedError":
            raise MockDataCorruptedError(error_message)
        else:
            raise MockDataError(error_message)


def clear_all_mocks(session) -> List[str]:
    """Clear mock files for all test cases when --regenerate-all-mocks is set.

    This is a session-level operation that clears all mock files across all test cases.
    Used during pytest session setup.

    Args:
        session: pytest session object containing all test items

    Returns:
        List of directories that had files cleared
    """
    from tests.llm.utils.test_case_utils import (
        HolmesTestCase,  # type: ignore[attr-defined]
    )

    print("\n🧹 Clearing mock files for --regenerate-all-mocks")

    cleared_directories = set()
    total_files_removed = 0

    # Extract all unique test case folders
    test_folders = set()
    for item in session.items:
        if (
            item.get_closest_marker("llm")
            and hasattr(item, "callspec")
            and "test_case" in item.callspec.params
        ):
            test_case = item.callspec.params["test_case"]
            if isinstance(test_case, HolmesTestCase):
                test_folders.add(test_case.folder)

    # Clear mock files from each folder
    for folder in test_folders:
        patterns = [
            os.path.join(folder, "*.txt"),
            os.path.join(folder, "*.json"),
        ]

        folder_files_removed = 0
        for pattern in patterns:
            for file_path in glob.glob(pattern):
                try:
                    os.remove(file_path)
                    folder_files_removed += 1
                    total_files_removed += 1
                except Exception as e:
                    logging.warning(f"Could not remove {file_path}: {e}")

        if folder_files_removed > 0:
            cleared_directories.add(folder)
            print(
                f"   ✅ Cleared {folder_files_removed} mock files from {os.path.basename(folder)}"
            )

    print(
        f"   📊 Total: Cleared {total_files_removed} files from {len(cleared_directories)} directories\n"
    )

    return list(cleared_directories)


class MockMetadata(BaseModel):
    """Metadata stored in mock files."""

    toolset_name: str
    tool_name: str
    match_params: Optional[Dict] = None  # None will match all params


class ToolMock(MockMetadata):
    """Complete mock data including metadata and result."""

    source_file: str
    return_value: StructuredToolResult


def sanitize_filename(original_file_name: str) -> str:
    """
    Sanitizes a potential filename to create a valid filename.
    http(s)://... -> scheme is removed.
    Characters not suitable for filenames are replaced with underscores.
    """
    # Remove scheme (http, https) if present
    filename = re.sub(r"^https?://", "", original_file_name, flags=re.IGNORECASE)

    # URL decode percent-encoded characters
    filename = urllib.parse.unquote(filename)

    # Replace characters not allowed in filenames
    filename = re.sub(r"[^\w.-]", "_", filename)

    # Consolidate multiple consecutive underscores into one
    filename = re.sub(r"__+", "_", filename)

    # Remove leading/trailing underscores and dots
    filename = filename.strip("_").strip(".")

    return filename


class MockFileManager:
    """Handles reading and writing mock files."""

    def __init__(self, test_case_folder: str, add_params_to_filename: bool = True):
        self.test_case_folder = test_case_folder
        self.add_params_to_filename = add_params_to_filename
        self._mock_cache = None  # Cache for loaded mocks
        self._lock = threading.Lock()

    def _get_mock_file_path(self, tool_name: str, params: Dict) -> str:
        """Generate the path for a mock file."""
        if self.add_params_to_filename:
            params_data = "_".join(str(params[k]) for k in sorted(params))
            params_data = f"_{params_data}"
        else:
            params_data = ""

        params_data = sanitize_filename(params_data)
        return os.path.join(self.test_case_folder, f"{tool_name}{params_data}.txt")

    def read_mock(self, tool_name: str, params: Dict) -> Optional[ToolMock]:
        """Read mock data for the given tool and parameters by matching parameters."""
        # Load all mocks and find a matching one
        all_mocks = self._load_all_mocks()
        tool_mocks = all_mocks.get(tool_name, [])

        logging.debug(f"Looking for mock for {tool_name} with params {params}")
        logging.debug(f"Found {len(tool_mocks)} mocks for {tool_name}")

        # Find a mock that matches the parameters
        for mock in tool_mocks:
            logging.debug(f"Checking mock from {mock.source_file}")
            logging.debug(f"  Mock params: {mock.match_params}")
            logging.debug(
                f"  Match result: {self._params_match(mock.match_params, params)}"
            )
            if self._params_match(mock.match_params, params):
                logging.debug(f"Found matching mock: {mock.source_file}")
                return mock

        logging.debug(f"No matching mock found for {tool_name}")
        return None

    def _params_match(self, mock_params: Optional[Dict], actual_params: Dict) -> bool:
        """Check if mock parameters match the actual parameters.

        If mock_params is None, it matches any parameters.
        Otherwise, all keys in mock_params must exist in actual_params with matching values.
        """
        if mock_params is None:
            return True

        # Check that all mock params exist in actual params with same values
        for key, value in mock_params.items():
            if key not in actual_params or actual_params[key] != value:
                return False

        return True

    def write_mock(
        self,
        tool_name: str,
        toolset_name: str,
        params: Dict,
        result: StructuredToolResult,
    ) -> str:
        """Write mock data to disk."""
        mock_metadata = MockMetadata(
            toolset_name=toolset_name, tool_name=tool_name, match_params=params
        )

        # Prepare structured output without data field
        structured_output = result.model_dump()
        content = structured_output.pop("data", None)

        mock_file_path = self._get_mock_file_path(tool_name, params)

        with open(mock_file_path, "w") as f:
            f.write(mock_metadata.model_dump_json() + "\n")
            f.write(json.dumps(structured_output) + "\n")
            if content:
                f.write(content)

        logging.info(f"Wrote mock file: {mock_file_path}")

        # Invalidate cache when new mock is written
        self._mock_cache = None

        return mock_file_path

    def clear_mocks_for_test(self, request: pytest.FixtureRequest) -> List[str]:
        """Clear all mock files for a single test case folder."""
        cleared_files = []
        patterns = [
            os.path.join(self.test_case_folder, "*.txt"),
            os.path.join(self.test_case_folder, "*.json"),
        ]

        for pattern in patterns:
            for file_path in glob.glob(pattern):
                try:
                    os.remove(file_path)
                    cleared_files.append(os.path.basename(file_path))
                except Exception as e:
                    logging.warning(f"Could not remove {file_path}: {e}")

        if cleared_files:
            # Track via user_properties for xdist compatibility
            request.node.user_properties.append(
                ("mocks_cleared", f"{self.test_case_folder}:{len(cleared_files)}")
            )
            logging.info(
                f"Cleared {len(cleared_files)} mock files from {self.test_case_folder}"
            )

            # Invalidate the cache since mocks were cleared
            self._mock_cache = None

        return cleared_files

    def _load_all_mocks(self) -> Dict[str, List[ToolMock]]:
        """Load all mock files in the directory and organize by tool name."""
        with self._lock:
            if self._mock_cache is not None:
                return self._mock_cache

            self._mock_cache = {}

            # Load all .txt files in the directory
            for file_path in glob.glob(os.path.join(self.test_case_folder, "*.txt")):
                try:
                    with open(file_path, "r") as f:
                        lines = f.readlines()
                        if len(lines) < 1:
                            continue

                        # Try to parse first line as JSON metadata
                        try:
                            first_line = lines[0].strip()
                            metadata = json.loads(first_line)
                            if "tool_name" not in metadata:
                                continue
                        except json.JSONDecodeError:
                            # Not a mock file, skip
                            continue

                        # This looks like a mock file, parse it
                        if len(lines) < 2:
                            continue

                        try:
                            structured_output = json.loads(lines[1].strip())
                        except json.JSONDecodeError:
                            # Check if this is an old format mock file
                            # Old format: Line 1 = metadata JSON, Line 2+ = raw output
                            # New format: Line 1 = metadata JSON, Line 2 = structured output JSON, Line 3+ = raw output
                            logging.error(
                                f"Mock file {file_path} appears to be in old format (missing structured JSON on second line). "
                                f"The mock file format was updated to include structured tool output metadata. "
                                f"Old format: Line 1 = metadata JSON, Line 2+ = raw output. "
                                f"New format: Line 1 = metadata JSON, Line 2 = structured output JSON, Line 3+ = raw output. "
                                f"This change was introduced in PR https://github.com/HolmesGPT/holmesgpt/pull/372. "
                                f"Please regenerate your mock files using --regenerate-all-mocks or manually update them to the new format."
                            )
                            raise MockDataCorruptedError(
                                f"Mock file {file_path} is in old format and needs to be updated (see PR #372)",
                                tool_name=metadata.get("tool_name", "unknown"),
                            ) from None

                        content = "".join(lines[2:]) if len(lines) > 2 else None
                        if content is not None:
                            structured_output["data"] = content

                        mock = ToolMock(
                            toolset_name=metadata.get("toolset_name", ""),
                            tool_name=metadata["tool_name"],
                            match_params=metadata.get("match_params"),
                            source_file=file_path,
                            return_value=StructuredToolResult(**structured_output),
                        )

                        # Add to cache organized by tool name
                        tool_name = metadata["tool_name"]
                        if tool_name not in self._mock_cache:
                            self._mock_cache[tool_name] = []
                        self._mock_cache[tool_name].append(mock)
                        logging.debug(
                            f"Loaded mock for {tool_name} from {file_path}: match_params={mock.match_params}"
                        )

                except MockDataError:
                    # Re-raise MockDataError types (including old format error) to propagate them
                    raise
                except Exception as e:
                    logging.warning(f"Failed to load mock file {file_path}: {e}")
                    continue

            return self._mock_cache


class MockableToolWrapper(Tool):
    """Wraps a single tool"""

    def __init__(
        self,
        tool: Tool,
        mode: MockMode,
        file_manager: MockFileManager,
        toolset_name: str,
        request: pytest.FixtureRequest,
    ):
        super().__init__(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            user_description=tool.user_description,
        )
        self._tool = tool
        self._mode = mode
        self._file_manager = file_manager
        self._toolset_name = toolset_name
        self._request = request

    def _call_live_invoke(
        self, params: Dict, context: ToolInvokeContext
    ) -> StructuredToolResult:
        """Call the tool in live mode."""
        logging.info(f"Calling live tool {self.name} with params: {params}")
        return self._tool.invoke(params, context)

    def _call_mock_invoke(self, params: Dict, context: ToolInvokeContext):
        # Mock mode: read from mock file
        mock = self._file_manager.read_mock(self.name, params)
        if not mock:
            # Debug logging before raising error
            import json

            logging.info(
                f"\n=== DEBUG: No matching mock found for tool '{self.name}' ==="
            )
            logging.info(
                f"Requested params: {json.dumps(params, indent=2, default=str)}"
            )

            # Check what mock files exist
            all_mocks = self._file_manager._load_all_mocks()
            tool_mocks = all_mocks.get(self.name, [])
            if tool_mocks:
                logging.info(f"Found {len(tool_mocks)} mock file(s) for this tool:")
                for i, mock_obj in enumerate(tool_mocks, 1):
                    logging.info(f"\n  Mock {i}:")
                    logging.info(f"    File: {mock_obj.source_file}")
                    if mock_obj.match_params:
                        logging.info(
                            f"    Match params: {json.dumps(mock_obj.match_params, indent=6, default=str)}"
                        )
                    else:
                        logging.info("    No match params (will match any params)")
            else:
                logging.info(f"No mock files found for tool '{self.name}'")
            logging.info("=== END DEBUG ===\n")

            # Check if there are any mock files for this tool that might be in old format
            pattern = os.path.join(
                self._file_manager.test_case_folder, f"{self.name}*.txt"
            )
            existing_files = glob.glob(pattern)

            if existing_files:
                # There are mock files, but none matched - could be old format
                error_msg = (
                    f"No mock data found for tool '{self.name}' with params: {params}. "
                    f"Found {len(existing_files)} mock file(s) for this tool, but none matched the parameters. "
                    f"This could be due to mock files being in the old format (missing structured JSON on line 2). "
                    f"See PR https://github.com/HolmesGPT/holmesgpt/pull/372 for format details."
                )
            else:
                error_msg = (
                    f"No mock data found for tool '{self.name}' with params: {params}"
                )

            raise MockDataNotFoundError(error_msg, tool_name=self.name)
        return mock.return_value

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool based on the current mode."""
        try:
            if self._mode == MockMode.GENERATE:
                try:
                    return self._call_mock_invoke(params, context)
                except MockDataNotFoundError as e:
                    logging.debug(str(e))

                result = self._call_live_invoke(params, context)
                # Write mock file
                mock_file_path = self._file_manager.write_mock(
                    tool_name=self.name,
                    toolset_name=self._toolset_name,
                    params=params,
                    result=result,
                )

                # Track generated mock via user_properties
                test_case = os.path.basename(self._file_manager.test_case_folder)
                mock_info = (
                    f"{test_case}:{self.name}:{os.path.basename(mock_file_path)}"
                )
                self._request.node.user_properties.append(
                    ("generated_mock_file", mock_info)
                )

                return result
            elif self._mode == MockMode.LIVE:
                # Live mode: just call the real tool
                return self._call_live_invoke(params, context)
            elif self._mode == MockMode.MOCK:
                return self._call_mock_invoke(params, context)
            else:
                raise ValueError(f"Unknown mock mode: {self._mode}")
        except MockDataError as e:
            # Track the mock error in pytest request context for later checking
            # We return an error result so the LLM can continue, but we'll fail the test after
            logging.error(f"Mock data error in tool {self.name}: {str(e)}")

            # Store the error in pytest request properties for later checking
            if self._request:
                error_data = {
                    "tool": self.name,
                    "error": str(e),
                    "type": type(e).__name__,
                }
                self._request.node.user_properties.append(
                    ("mock_data_error", error_data)
                )
                logging.info(f"Stored mock error in user_properties: {error_data}")
            else:
                logging.warning(
                    f"No request object available to store mock error for tool {self.name}"
                )

            # Return error result so LLM can continue (but test will fail later)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Mock data error: {str(e)}",
                data=None,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        """Get a one-line description of the tool with parameters."""
        return self._tool.get_parameterized_one_liner(params)


class SimplifiedMockToolset(Toolset):
    """Simplified mock toolset for testing."""

    original_toolset_type: Type[Toolset]

    def get_status(self):
        return ToolsetStatusEnum.ENABLED

    def get_example_config(self) -> Dict[str, Any]:
        return {}


class MockToolsetManager:
    """Manages mock toolsets for testing."""

    def __init__(
        self,
        test_case_folder: str,
        mock_generation_config: MockGenerationConfig,
        request: Optional[pytest.FixtureRequest] = None,
        mock_policy: str = "inherit",
        mock_overrides: Optional[Dict[str, str]] = None,
        allow_toolset_failures: bool = False,
    ):
        self.test_case_folder = test_case_folder
        self.request = request
        self.mock_overrides = mock_overrides or {}
        self.mock_generation_config = mock_generation_config
        self.allow_toolset_failures = allow_toolset_failures

        # Coerce mock_policy string to MockPolicy enum, falling back to INHERIT for unknown values
        if isinstance(mock_policy, str):
            try:
                mock_policy_enum = MockPolicy(mock_policy)
            except (ValueError, KeyError):
                # Fall back to INHERIT for unknown/legacy values
                mock_policy_enum = MockPolicy.INHERIT
        elif isinstance(mock_policy, MockPolicy):
            mock_policy_enum = mock_policy
        else:
            # Default to INHERIT for any other type
            mock_policy_enum = MockPolicy.INHERIT

        # Determine the default mode based on mock_policy
        if mock_policy_enum == MockPolicy.ALWAYS_MOCK:
            if mock_generation_config.mode == MockMode.GENERATE:
                pytest.skip(
                    "Test has fixed mocks (mock_policy='always_mock') so this test will be skipped. If you want to override mocks for this test and generate from scratch, change the mock_policy for this test temporarily."
                )
            else:
                self.default_mode = MockMode.MOCK
        elif mock_policy_enum == MockPolicy.NEVER_MOCK:
            if mock_generation_config.mode != MockMode.LIVE:
                pytest.skip(
                    "Test requires live execution (mock_policy=NEVER_MOCK) but RUN_LIVE is not enabled"
                )
            self.default_mode = MockMode.LIVE
        else:  # INHERIT or any other value
            self.default_mode = mock_generation_config.mode

        # Initialize components
        self.file_manager = MockFileManager(test_case_folder)

        # Load and configure toolsets
        self._initialize_toolsets()

    def _get_toolset_mode(self, toolset_name: str) -> MockMode:
        """Get the mock mode for a specific toolset, considering overrides."""
        override = self.mock_overrides.get(toolset_name)

        if override:
            if override == MockPolicy.ALWAYS_MOCK.value:
                if self.mock_generation_config.mode == MockMode.GENERATE:
                    # Can't force mock when generating - use GENERATE mode
                    return MockMode.GENERATE
                return MockMode.MOCK
            elif override == MockPolicy.NEVER_MOCK.value:
                return MockMode.LIVE
            # If override is "inherit" or unknown, use default

        return self.default_mode

    def _initialize_toolsets(self):
        """Initialize and configure toolsets."""

        mock_dal = load_mock_dal(
            test_case_folder=Path(self.test_case_folder),
            generate_mocks=self.mock_generation_config.generate_mocks,
        )
        # Load builtin toolsets
        builtin_toolsets = load_builtin_toolsets(mock_dal)

        # Load custom toolsets from YAML if present
        config_path = os.path.join(self.test_case_folder, "toolsets.yaml")
        custom_definitions = self._load_custom_toolsets(config_path)

        # Always load default toolsets.yaml
        default_config_path = os.path.join(
            os.path.dirname(__file__), "default_toolsets.yaml"
        )
        default_definitions = self._load_custom_toolsets(default_config_path)

        # If custom toolsets.yaml exists, merge with defaults (custom takes precedence)
        # Otherwise, use defaults only
        if custom_definitions:
            # Merge: custom definitions override defaults for same toolset names
            # Add default definitions for toolsets not in custom
            custom_names = {d.name for d in custom_definitions}
            merged_definitions = list(custom_definitions)
            for default_def in default_definitions:
                if default_def.name not in custom_names:
                    merged_definitions.append(default_def)
            custom_definitions = merged_definitions
        else:
            # No custom toolsets.yaml, use defaults
            custom_definitions = default_definitions

        # Configure builtin toolsets with custom definitions
        self.toolsets = self._configure_toolsets(builtin_toolsets, custom_definitions)

        # Wrap tools for enabled toolsets based on mode
        self._wrap_enabled_toolsets()

    def _load_custom_toolsets(self, config_path: str) -> List[Toolset]:
        """Load custom toolsets from a YAML file."""
        if not os.path.isfile(config_path):
            return []
        return load_toolsets_from_file(toolsets_path=config_path, strict_check=False)

    def _configure_toolsets(
        self, builtin_toolsets: List[Toolset], custom_definitions: List[Toolset]
    ) -> List[Toolset]:
        """Configure builtin toolsets with custom definitions."""
        configured = []

        # First, validate that all custom definitions reference existing toolsets
        builtin_names = {ts.name for ts in builtin_toolsets}
        for definition in custom_definitions:
            if definition.name not in builtin_names:
                raise RuntimeError(
                    f"Toolset '{definition.name}' referenced in toolsets.yaml does not exist. "
                    f"Available toolsets: {', '.join(sorted(builtin_names))}"
                )

        mock_dal = load_mock_dal(
            test_case_folder=Path(self.test_case_folder),
            generate_mocks=self.mock_generation_config.generate_mocks,
            initialize_base=False,
        )
        for toolset in builtin_toolsets:
            # Replace RunbookToolset with one that has test folder search path
            if toolset.name == "runbook":
                from holmes.plugins.toolsets.runbook.runbook_fetcher import (
                    RunbookToolset,
                )

                # Create new RunbookToolset with test folder as additional search path
                new_runbook_toolset = RunbookToolset(
                    dal=mock_dal, additional_search_paths=[self.test_case_folder]
                )
                new_runbook_toolset.enabled = toolset.enabled
                new_runbook_toolset.status = toolset.status
                # Preserve any existing config but add our search paths
                if toolset.config:
                    new_runbook_toolset.config.update(toolset.config)
                toolset = new_runbook_toolset
            elif toolset.name == "kubernetes/core":
                if not isinstance(toolset, YAMLToolset):
                    raise ValueError(
                        f"Expected kubernetes/core to be YAMLToolset, got {type(toolset)}"
                    )
                yaml_toolset: YAMLToolset = toolset

                # Block secret access to prevent LLM from reading code hints in secrets
                security_check = """# Security check (automatically added by test framework - can be ignored)
if [ "{{ kind }}" = "secret" ] || [ "{{ kind }}" = "secrets" ]; then echo "Not allowed to get kubernetes secrets"; exit 1; fi
# Actual command follows:
"""

                for tool in yaml_toolset.tools:
                    if not isinstance(tool, YAMLTool):
                        raise ValueError(
                            f"Expected all tools in kubernetes/core to be YAMLTool, got {type(tool)}"
                        )

                    # Check if tool has command or script
                    if tool.command is not None:
                        tool.command = security_check + tool.command
                    elif tool.script is not None:
                        tool.script = security_check + tool.script
                    else:
                        raise ValueError(
                            f"Tool '{tool.name}' in kubernetes/core has neither command nor script defined"
                        )

            # Apply custom configuration if available
            definition = next(
                (d for d in custom_definitions if d.name == toolset.name), None
            )
            if definition:
                # If there's a custom definition, use it to determine enabled state
                toolset.config = definition.config
                toolset.enabled = definition.enabled
            elif custom_definitions:
                # toolsets.yaml exists (custom_definitions is non-empty) but this toolset isn't explicitly listed
                # Disable it to ensure only explicitly enabled toolsets are loaded when toolsets.yaml is present
                toolset.enabled = False

            # Add all toolsets to configured list
            configured.append(toolset)

            # Check prerequisites for enabled toolsets with timeout
            # Only check prerequisites in LIVE mode - for MOCK/GENERATE modes we don't need real connections
            if toolset.enabled:
                toolset_mode = self._get_toolset_mode(toolset.name)
                if toolset_mode == MockMode.LIVE or toolset_mode == MockMode.GENERATE:
                    try:
                        # TODO: add timeout
                        toolset.check_prerequisites()

                        # If this toolset was explicitly enabled in the test config but failed prerequisites,
                        # the test should fail (unless allow_toolset_failures is True)
                        if (
                            definition
                            and definition.enabled
                            and toolset.status != ToolsetStatusEnum.ENABLED
                            and not self.allow_toolset_failures
                        ):
                            raise RuntimeError(
                                f"Toolset '{toolset.name}' was explicitly enabled in toolsets.yaml "
                                f"but failed prerequisites check: {toolset.error or 'Unknown error'}"
                            )
                    except Exception as e:
                        # If this toolset was explicitly enabled in the test config, re-raise the error
                        if definition and definition.enabled:
                            raise RuntimeError(
                                f"Toolset '{toolset.name}' was explicitly enabled in toolsets.yaml "
                                f"but failed prerequisites check: {str(e)}"
                            ) from e
                        else:
                            # Otherwise just log it - it was enabled by default, not explicitly
                            logging.error(
                                f"check_prerequisites failed for toolset {toolset.name}.",
                                exc_info=True,
                            )
                else:
                    # In MOCK mode, just set status to ENABLED for enabled toolsets
                    toolset.status = ToolsetStatusEnum.ENABLED

        return configured

    def _wrap_enabled_toolsets(self):
        """Wrap tools with mock-aware wrappers for enabled toolsets based on their specific modes."""
        # For each enabled toolset, determine its specific mode and wrap if needed
        for i, toolset in enumerate(self.toolsets):
            # Only wrap enabled toolsets
            if toolset.status == ToolsetStatusEnum.ENABLED:
                # Never mock the runbook toolset - it needs to actually fetch runbooks
                if toolset.name == "runbook":
                    continue
                # Never mock the core_investigation toolset - TodoWrite and other investigation tools should always run live
                if toolset.name == "core_investigation":
                    continue

                # Determine the mode for this specific toolset
                toolset_mode = self._get_toolset_mode(toolset.name)

                # Skip wrapping if toolset is in LIVE mode
                if toolset_mode == MockMode.LIVE:
                    continue

                # Create wrapped tools with the toolset-specific mode
                wrapped_tools = [
                    MockableToolWrapper(
                        tool=tool,
                        mode=toolset_mode,
                        file_manager=self.file_manager,
                        toolset_name=toolset.name,
                        request=self.request,
                    )
                    for tool in toolset.tools
                ]

                # Create simplified mock toolset and replace the original
                mock_toolset = SimplifiedMockToolset(
                    name=toolset.name,
                    prerequisites=toolset.prerequisites,
                    tools=wrapped_tools,
                    description=toolset.description,
                    llm_instructions=toolset.llm_instructions,
                    config=toolset.config,
                    original_toolset_type=type(toolset),
                )
                mock_toolset.status = ToolsetStatusEnum.ENABLED
                self.toolsets[i] = mock_toolset


# For backward compatibility
MockToolsets = MockToolsetManager


def report_mock_operations(
    config, mock_tracking_data: Dict[str, List], terminalreporter=None
) -> None:
    """Report mock file operations and statistics."""
    # Use default parameter to safely handle missing options
    generate_mocks = False
    regenerate_all_mocks = False

    try:
        generate_mocks = config.getoption("--generate-mocks", default=False)
        regenerate_all_mocks = config.getoption("--regenerate-all-mocks", default=False)
    except (AttributeError, ValueError):
        # Options not available, use defaults
        pass

    if not generate_mocks and not regenerate_all_mocks:
        return

    regenerate_mode = regenerate_all_mocks
    generated_mocks = mock_tracking_data["generated_mocks"]
    mock_failures = mock_tracking_data["mock_failures"]

    # If no terminalreporter, skip output
    if not terminalreporter:
        return

    # Header
    _safe_print(terminalreporter, f"\n{'=' * 80}")
    _safe_print(
        terminalreporter,
        f"{'🔄 MOCK REGENERATION SUMMARY' if regenerate_mode else '🔧 MOCK GENERATION SUMMARY'}",
    )
    _safe_print(terminalreporter, f"{'=' * 80}")

    # Note: Cleared directories are now handled by shared_test_infrastructure fixture
    # and reported during setup phase to ensure single execution across workers

    # Generated mocks
    if generated_mocks:
        _safe_print(
            terminalreporter, f"✅ Generated {len(generated_mocks)} mock files:\n"
        )

        # Group by test case
        by_test_case: Dict[str, List[str]] = {}
        for mock_info in generated_mocks:
            parts = mock_info.split(":", 2)
            if len(parts) == 3:
                test_case, tool_name, filename = parts
                by_test_case.setdefault(test_case, []).append(
                    f"{tool_name} -> {filename}"
                )

        for test_case, mock_files in sorted(by_test_case.items()):
            _safe_print(terminalreporter, f"📁 {test_case}:")
            for mock_file in mock_files:
                _safe_print(terminalreporter, f"   - {mock_file}")
            _safe_print(terminalreporter)
    else:
        mode_text = "regeneration" if regenerate_mode else "generation"
        _safe_print(
            terminalreporter,
            f"✅ Mock {mode_text} was enabled but no new mock files were created",
        )

    # Failures
    if mock_failures:
        _safe_print(
            terminalreporter, f"⚠️  {len(mock_failures)} mock-related failures occurred:"
        )
        for failure in mock_failures:
            _safe_print(terminalreporter, f"   - {failure}")
        _safe_print(terminalreporter)

    # Checklist
    checklist = [
        "Review generated mock files before committing",
        "Ensure mock data represents realistic scenarios",
        "Check data consistency across related mocks (e.g., if a pod appears in",
        "  one mock, it should appear in all related mocks from the same test run)",
        "Verify timestamps, IDs, and names match between interconnected mock files",
        "If pod/resource names change across tool calls, regenerate ALL mocks with --regenerate-all-mocks",
    ]

    _safe_print(terminalreporter, "📋 REVIEW CHECKLIST:")
    for item in checklist:
        _safe_print(terminalreporter, f"   □ {item}")
    _safe_print(terminalreporter, "=" * 80)


def _safe_print(terminalreporter, message: str = "") -> None:
    """Safely print to terminal reporter to avoid I/O errors"""
    try:
        terminalreporter.write_line(message)
    except Exception:
        # If write_line fails, try direct write
        try:
            terminalreporter._tw.write(message + "\n")
        except Exception:
            # Last resort - ignore if all writing fails
            pass


# Export list
__all__ = [
    "MockMode",
    "MockPolicy",
    "MockGenerationConfig",
    "MockToolsetManager",
    "MockToolsets",
    "MockDataError",
    "MockDataNotFoundError",
    "MockDataCorruptedError",
    "MockValidationError",
    "MockFileManager",
    "MockableToolWrapper",
    "sanitize_filename",
    "clear_all_mocks",
    "report_mock_operations",
]
