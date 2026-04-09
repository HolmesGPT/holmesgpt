import logging
from typing import List, Optional

import sentry_sdk

from holmes.core.init_event import EventCallback, StatusEvent, StatusEventKind
from holmes.core.tools import (
    Tool,
    Toolset,
    ToolsetStatusEnum,
)

display_logger = logging.getLogger("holmes.display.tool_executor")


class ToolExecutor:
    def __init__(self, toolsets: List[Toolset], on_event: EventCallback = None):
        # TODO: expose function for this instead of callers accessing directly
        self.toolsets = toolsets

        self.enabled_toolsets: list[Toolset] = [
            ts for ts in toolsets if ts.status == ToolsetStatusEnum.ENABLED
        ]

        toolsets_by_name: dict[str, Toolset] = {}
        for ts in self.enabled_toolsets:
            if ts.name in toolsets_by_name:
                msg = f"Overriding toolset '{ts.name}'!"
                display_logger.warning(msg)
                if on_event is not None:
                    on_event(StatusEvent(kind=StatusEventKind.TOOL_OVERRIDE, name=ts.name, message=msg))
            toolsets_by_name[ts.name] = ts

        self.tools_by_name: dict[str, Tool] = {}
        self._tool_to_toolset: dict[str, Toolset] = {}
        for ts in toolsets_by_name.values():
            for tool in ts.tools:
                if tool.icon_url is None and ts.icon_url is not None:
                    tool.icon_url = ts.icon_url
                if tool.name in self.tools_by_name:
                    msg = f"Overriding existing tool '{tool.name} with new tool from {ts.name} at {ts.path}'!"
                    display_logger.warning(msg)
                    if on_event is not None:
                        on_event(StatusEvent(kind=StatusEventKind.TOOL_OVERRIDE, name=tool.name, message=msg))
                self.tools_by_name[tool.name] = tool
                self._tool_to_toolset[tool.name] = ts

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        if name in self.tools_by_name:
            return self.tools_by_name[name]
        logging.warning(f"could not find tool {name}. skipping")
        return None

    def get_toolset_name(self, tool_name: str) -> Optional[str]:
        """Return the toolset name that provides a given tool, or None."""
        ts = self._tool_to_toolset.get(tool_name)
        return ts.name if ts else None

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

    def clone_with_extra_tools(self, extra_tools: List[Tool]) -> "ToolExecutor":
        """Create a shallow clone with additional tools registered.

        The clone shares the same toolsets and base tools but adds extra_tools
        on top. The original ToolExecutor is never mutated.

        This is used to inject frontend tools (FrontendPauseTool) on a
        per-request basis without modifying the shared ToolExecutor.
        """
        clone = object.__new__(ToolExecutor)
        clone.toolsets = self.toolsets
        clone.enabled_toolsets = self.enabled_toolsets
        clone.tools_by_name = dict(self.tools_by_name)
        clone._tool_to_toolset = dict(self._tool_to_toolset)

        for tool in extra_tools:
            if tool.name in clone.tools_by_name:
                logging.warning(
                    f"Frontend tool '{tool.name}' overrides existing tool"
                )
            clone.tools_by_name[tool.name] = tool
            # No toolset mapping — frontend tools don't belong to a toolset,
            # so ensure_toolset_initialized() returns None (no-op) for them.

        return clone

    @sentry_sdk.trace
    def get_all_tools_openai_format(
        self,
        include_restricted: bool = True,
    ):
        """Get all tools in OpenAI format.

        Args:
            include_restricted: If False, filter out tools marked as restricted.
                               Set to True when runbook is in use or restricted
                               tools are explicitly enabled.
        """
        tools = []
        for tool in self.tools_by_name.values():
            # Filter out restricted tools if not authorized
            if not include_restricted and tool._is_restricted():
                continue
            tools.append(tool.get_openai_format())
        return tools
