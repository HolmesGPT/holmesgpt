from holmes.core.llm import LLM
from holmes.core.models import format_tool_result_data
from holmes.core.tools import StructuredToolResult


def count_tool_response_tokens(
    llm: LLM,
    structured_tool_result: StructuredToolResult,
    tool_call_id: str,
    tool_name: str,
) -> int:
    message = {
        "role": "tool",
        "content": format_tool_result_data(
            tool_result=structured_tool_result,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        ),
    }
    tokens = llm.count_tokens([message])
    return tokens.total_tokens
