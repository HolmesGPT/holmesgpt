import sentry_sdk

from holmes.core.models import ToolCallResult


def capture_toolcall_contains_too_many_tokens(
    tool_call_result: ToolCallResult, token_count: int, max_allowed_token_count: int
):
    sentry_sdk.capture_message(
        f"Tool call {tool_call_result.tool_name} contains too many tokens",
        level="warning",
        tags={
            "tool_name": tool_call_result.tool_name,
            "tool_original_token_count": token_count,
            "tool_max_allowed_token_count": max_allowed_token_count,
            "tool_description": tool_call_result.description,
        },
    )
