from holmes.core.tool_search import LOAD_TOOLS_NAME, LOAD_TOOLS_TOOL


def test_load_tools_tool_is_a_function_tool():
    # Must be a normal OpenAI function tool so HolmesGPT can execute it and so it
    # works across every provider (no Anthropic server-tool / beta dependency).
    assert LOAD_TOOLS_TOOL["type"] == "function"
    assert LOAD_TOOLS_TOOL["function"]["name"] == LOAD_TOOLS_NAME
    assert "query" in LOAD_TOOLS_TOOL["function"]["parameters"]["properties"]
    assert LOAD_TOOLS_TOOL["function"]["parameters"]["required"] == ["query"]
