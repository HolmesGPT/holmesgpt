"""Use Holmes' existing tool and MCP lifecycle, including approval checks."""

from typing import TYPE_CHECKING, Protocol, cast

from holmes.core.tool_errors import classify_tool_error
from holmes.core.tools import PrerequisiteCacheMode, ToolInvokeContext, ToolsetTag
from holmes.decision.models import Candidate, ToolOutput, ToolSpec, ToolStatus

if TYPE_CHECKING:
    from holmes.config import Config
    from holmes.core.llm import LLM


class ToolGateway(Protocol):
    def catalog(self, refresh: bool = True) -> list[ToolSpec]: ...

    def execute(self, candidate: Candidate) -> ToolOutput: ...


class HolmesTools:
    def __init__(
        self, config: "Config", llm: "LLM", user_id: str,
        allowed_tools: set[str] | None = None,
        retry_safe_tools: set[str] | None = None,
    ):
        self.config = config
        self.llm = llm
        self.user_id = user_id
        self.allowed_tools = allowed_tools
        self.retry_safe_tools = retry_safe_tools or set()
        self.tags = [ToolsetTag.CORE, ToolsetTag.CLI]
        self.executor = config.create_tool_executor(
            toolset_tag_filter=self.tags, enable_all_toolsets_possible=False,
            prerequisite_cache=PrerequisiteCacheMode.DISABLED, reuse_executor=True,
        )

    def catalog(self, refresh: bool = True) -> list[ToolSpec]:
        if refresh:
            self.config.refresh_tool_executor(
                toolset_tag_filter=self.tags, enable_all_toolsets_possible=False,
            )
            executor = self.config.cached_tool_executor
            if executor is None:
                raise RuntimeError("Refreshed tool executor is unavailable")
            self.executor = executor
        specs = []
        for item in self.executor.get_all_tools_openai_format(user_id=self.user_id):
            tool = item["function"]
            if self.allowed_tools is not None and tool["name"] not in self.allowed_tools:
                continue
            specs.append(ToolSpec(
                name=tool["name"], description=tool.get("description", ""),
                parameters=tool.get("parameters", {"type": "object", "properties": {}}),
                source=self.executor.get_toolset_name(tool["name"], self.user_id) or "holmes",
                retry_safe=tool["name"] in self.retry_safe_tools,
            ))
        return specs

    def execute(self, candidate: Candidate) -> ToolOutput:
        if self.allowed_tools is not None and candidate.tool not in self.allowed_tools:
            return ToolOutput(status="approval_required", error="Tool is outside the session allowlist")
        error = self.executor.ensure_toolset_initialized(candidate.tool)
        if error:
            return ToolOutput(status="error", error=error)
        tool = self.executor.get_tool_by_name(candidate.tool, self.user_id)
        if tool is None:
            return ToolOutput(status="error", error="Tool was removed or disabled")
        result = tool.invoke(candidate.arguments.copy(), ToolInvokeContext(
            llm=self.llm, tool_call_id=candidate.fingerprint,
            tool_name=candidate.tool, max_token_count=self.llm.get_max_token_count_for_single_tool(),
            user_approved=False, request_context={"user_id": self.user_id},
        ))
        status = result.status.value
        if status not in {"success", "no_data", "error", "approval_required"}:
            status = "approval_required"
        return ToolOutput(status=cast(ToolStatus, status), data=result.get_stringified_data(), error=result.error,
                          error_kind=result.error_kind, retry_after_seconds=result.retry_after_seconds)


def tool_error_output(exc: Exception) -> ToolOutput:
    details = classify_tool_error(exc)
    return ToolOutput(status="error", error=f"{type(exc).__name__}: {exc}",
                      error_kind=details.kind, retry_after_seconds=details.retry_after_seconds)
