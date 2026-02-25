import logging
from typing import List, Optional

import sentry_sdk

from holmes.core.tools import (
    Tool,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.toolset_utils import filter_out_default_logging_toolset


class ToolExecutor:
    def __init__(self, toolsets: List[Toolset]):
        # TODO: expose function for this instead of callers accessing directly
        self.toolsets = toolsets

        enabled_toolsets: list[Toolset] = list(
            filter(
                lambda toolset: toolset.status == ToolsetStatusEnum.ENABLED,
                toolsets,
            )
        )

        self.enabled_toolsets: list[Toolset] = filter_out_default_logging_toolset(
            enabled_toolsets
        )

        toolsets_by_name: dict[str, Toolset] = {}
        for ts in self.enabled_toolsets:
            if ts.name in toolsets_by_name:
                logging.warning(f"Overriding toolset '{ts.name}'!")
            toolsets_by_name[ts.name] = ts

        self.tools_by_name: dict[str, Tool] = {}
        self._tool_to_toolset: dict[str, Toolset] = {}
        for ts in toolsets_by_name.values():
            for tool in ts.tools:
                if tool.icon_url is None and ts.icon_url is not None:
                    tool.icon_url = ts.icon_url
                if tool.name in self.tools_by_name:
                    logging.warning(
                        f"Overriding existing tool '{tool.name} with new tool from {ts.name} at {ts.path}'!"
                    )
                self.tools_by_name[tool.name] = tool
                self._tool_to_toolset[tool.name] = ts

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        if name in self.tools_by_name:
            return self.tools_by_name[name]
        logging.warning(f"could not find tool {name}. skipping")
        return None

    def ensure_toolset_initialized(self, tool_name: str) -> Optional[str]:
        """Ensure the toolset containing the given tool is lazily initialized.

        For toolsets loaded from cache without full initialization, this triggers
        the deferred prerequisite checks (callable and command prerequisites)
        on first tool use.

        Returns None on success, or an error message string on failure.
        """
        toolset = self._tool_to_toolset.get(tool_name)
        if toolset is None:
            return None

        if toolset.needs_initialization:
            if not toolset.lazy_initialize():
                error_msg = f"Toolset '{toolset.name}' failed to initialize: {toolset.error}"
                logging.error(error_msg)
                return error_msg
        elif toolset.status == ToolsetStatusEnum.FAILED:
            # Toolset was already initialized but failed — don't let tools execute
            error_msg = f"Toolset '{toolset.name}' is unavailable: {toolset.error}"
            logging.error(error_msg)
            return error_msg

        return None

    @sentry_sdk.trace
    def get_all_tools_openai_format(
        self,
        target_model: str,
        include_restricted: bool = True,
    ):
        """Get all tools in OpenAI format.

        Args:
            target_model: The target LLM model name
            include_restricted: If False, filter out tools marked as restricted.
                               Set to True when runbook is in use or restricted
                               tools are explicitly enabled.
        """
        tools = []
        for tool in self.tools_by_name.values():
            # Filter out restricted tools if not authorized
            if not include_restricted and tool._is_restricted():
                continue
            tools.append(tool.get_openai_format(target_model=target_model))
        return tools
