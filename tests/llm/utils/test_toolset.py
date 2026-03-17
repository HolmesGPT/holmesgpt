# type: ignore
import logging
import os
from pathlib import Path
from typing import List, Optional, Type

from holmes.core.tools import (
    Toolset,
    ToolsetStatusEnum,
    YAMLTool,
    YAMLToolset,
)
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_file
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset
from tests.llm.utils.mock_dal import load_test_dal


class ToolsetPrerequisiteError(Exception):
    """Raised when an explicitly enabled toolset fails its prerequisite check.

    This is classified as a setup failure rather than a test failure because
    it indicates infrastructure issues, not LLM evaluation issues.
    """

    def __init__(self, toolset_name: str, error_detail: str):
        self.toolset_name = toolset_name
        self.error_detail = error_detail
        message = (
            f"Toolset '{toolset_name}' was explicitly enabled in toolsets.yaml "
            f"but failed prerequisites check: {error_detail}"
        )
        super().__init__(message)


class TestToolsetManager:
    """Manages toolsets for eval tests. Always runs tools live (no mocking)."""

    def __init__(
        self,
        test_case_folder: str,
        allow_toolset_failures: bool = False,
        toolsets_config_path: Optional[str] = None,
    ):
        self.test_case_folder = test_case_folder
        self.allow_toolset_failures = allow_toolset_failures
        self.toolsets_config_path = toolsets_config_path

        # Initialize components
        self._initialize_toolsets()

    def _initialize_toolsets(self):
        """Initialize and configure toolsets."""

        dal = load_test_dal(
            test_case_folder=Path(self.test_case_folder),
        )
        # Load builtin toolsets
        builtin_toolsets = load_builtin_toolsets(dal)

        # Load custom toolsets from YAML if present
        config_path = self.toolsets_config_path or os.path.join(
            self.test_case_folder, "toolsets.yaml"
        )
        custom_definitions = self._load_custom_toolsets(config_path)

        # Always load default toolsets.yaml
        default_config_path = os.path.join(
            os.path.dirname(__file__), "default_toolsets.yaml"
        )
        default_definitions = self._load_custom_toolsets(default_config_path)

        # If custom toolsets.yaml exists, merge with defaults (custom takes precedence)
        if custom_definitions:
            custom_names = {d.name for d in custom_definitions}
            merged_definitions = list(custom_definitions)
            for default_def in default_definitions:
                if default_def.name not in custom_names:
                    merged_definitions.append(default_def)
            custom_definitions = merged_definitions
        else:
            custom_definitions = default_definitions

        # Configure builtin toolsets with custom definitions
        self.toolsets = self._configure_toolsets(builtin_toolsets, custom_definitions)

    def _load_custom_toolsets(self, config_path: str) -> List[Toolset]:
        """Load custom toolsets from a YAML file."""
        if not os.path.isfile(config_path):
            return []
        return load_toolsets_from_file(toolsets_path=config_path, strict_check=False)

    def _configure_toolsets(
        self, builtin_toolsets: List[Toolset], custom_definitions: List[Toolset]
    ) -> List[Toolset]:
        """Configure builtin toolsets with custom definitions."""
        from holmes.plugins.toolsets.database.database import DatabaseToolset
        from holmes.plugins.toolsets.http.http_toolset import HttpToolset
        from holmes.plugins.toolsets.mongodb.mongodb import MongoDBToolset

        configured = []

        # Validate that all custom definitions reference existing toolsets
        # (except for dynamically-created toolsets: HTTP, MCP, Database, MongoDB)
        builtin_names = {ts.name for ts in builtin_toolsets}
        for definition in custom_definitions:
            if isinstance(
                definition,
                (HttpToolset, RemoteMCPToolset, DatabaseToolset, MongoDBToolset),
            ):
                continue
            if definition.name not in builtin_names:
                raise RuntimeError(
                    f"Toolset '{definition.name}' referenced in toolsets.yaml does not exist. "
                    f"Available toolsets: {', '.join(sorted(builtin_names))}"
                )

        # Collect dynamically-created toolsets from custom definitions
        http_toolsets = {
            d.name: d for d in custom_definitions if isinstance(d, HttpToolset)
        }
        mcp_toolsets = {
            d.name: d for d in custom_definitions if isinstance(d, RemoteMCPToolset)
        }
        database_toolsets = {
            d.name: d
            for d in custom_definitions
            if isinstance(d, (DatabaseToolset, MongoDBToolset))
        }

        dal = load_test_dal(
            test_case_folder=Path(self.test_case_folder),
            initialize_base=False,
        )
        for toolset in builtin_toolsets:
            # Skip built-in toolsets that are replaced by dynamic toolsets
            if (
                toolset.name in http_toolsets
                or toolset.name in mcp_toolsets
                or toolset.name in database_toolsets
            ):
                continue
            # Replace RunbookToolset with one that has test folder search path
            if toolset.name == "runbook":
                from holmes.plugins.toolsets.runbook.runbook_fetcher import (
                    RunbookToolset,
                )

                new_runbook_toolset = RunbookToolset(
                    dal=dal, additional_search_paths=[self.test_case_folder]
                )
                new_runbook_toolset.enabled = toolset.enabled
                new_runbook_toolset.status = toolset.status
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
                toolset.config = definition.config
                toolset.enabled = definition.enabled
            elif custom_definitions:
                # toolsets.yaml exists but this toolset isn't explicitly listed - disable it
                toolset.enabled = False

            configured.append(toolset)

            # Check prerequisites for enabled toolsets
            if toolset.enabled:
                try:
                    toolset.check_prerequisites()

                    if (
                        definition
                        and definition.enabled
                        and toolset.status != ToolsetStatusEnum.ENABLED
                        and not self.allow_toolset_failures
                    ):
                        raise ToolsetPrerequisiteError(
                            toolset_name=toolset.name,
                            error_detail=toolset.error or "Unknown error",
                        )
                except ToolsetPrerequisiteError:
                    raise
                except Exception as e:
                    if definition and definition.enabled:
                        raise ToolsetPrerequisiteError(
                            toolset_name=toolset.name,
                            error_detail=str(e),
                        ) from e
                    else:
                        logging.error(
                            f"check_prerequisites failed for toolset {toolset.name}.",
                            exc_info=True,
                        )

        # Add HTTP toolsets from custom definitions
        for http_toolset in http_toolsets.values():
            configured.append(http_toolset)

            if http_toolset.enabled:
                try:
                    http_toolset.check_prerequisites()

                    if (
                        http_toolset.status != ToolsetStatusEnum.ENABLED
                        and not self.allow_toolset_failures
                    ):
                        raise ToolsetPrerequisiteError(
                            toolset_name=http_toolset.name,
                            error_detail=http_toolset.error or "Unknown error",
                        )
                except ToolsetPrerequisiteError:
                    raise
                except Exception as e:
                    raise ToolsetPrerequisiteError(
                        toolset_name=http_toolset.name,
                        error_detail=str(e),
                    ) from e

        # Add MCP toolsets from custom definitions
        for mcp_toolset in mcp_toolsets.values():
            configured.append(mcp_toolset)

            if mcp_toolset.enabled:
                try:
                    mcp_toolset.check_prerequisites()

                    if (
                        mcp_toolset.status != ToolsetStatusEnum.ENABLED
                        and not self.allow_toolset_failures
                    ):
                        raise ToolsetPrerequisiteError(
                            toolset_name=mcp_toolset.name,
                            error_detail=mcp_toolset.error or "Unknown error",
                        )
                except ToolsetPrerequisiteError:
                    raise
                except Exception as e:
                    raise ToolsetPrerequisiteError(
                        toolset_name=mcp_toolset.name,
                        error_detail=str(e),
                    ) from e

        # Add Database/MongoDB toolsets from custom definitions
        for db_toolset in database_toolsets.values():
            configured.append(db_toolset)

            if db_toolset.enabled:
                try:
                    db_toolset.check_prerequisites()

                    if (
                        db_toolset.status != ToolsetStatusEnum.ENABLED
                        and not self.allow_toolset_failures
                    ):
                        raise ToolsetPrerequisiteError(
                            toolset_name=db_toolset.name,
                            error_detail=db_toolset.error or "Unknown error",
                        )
                except ToolsetPrerequisiteError:
                    raise
                except Exception as e:
                    raise ToolsetPrerequisiteError(
                        toolset_name=db_toolset.name,
                        error_detail=str(e),
                    ) from e

        return configured
