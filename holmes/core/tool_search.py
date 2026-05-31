"""Client-side tool search (progressive tool disclosure).

When ``HOLMES_TOOL_SEARCH_ENABLED`` is set, heavy tool schemas (MCP toolsets) are
NOT loaded into the model's context up front. Instead the model is given a single
``load_tools`` function tool; it calls that with a query to discover and load the
tools it needs, and they become available on the next step.

This is a deliberate alternative to Anthropic's server-side tool-search /
``defer_loading`` beta: that path is mis-handled by LiteLLM (server_tool_use is
converted to a client tool_use and result blocks are dropped — BerriAI/litellm
issues #17737 and #28083), which breaks multi-step tool use through Bedrock-backed
gateways. ``load_tools`` is a plain function tool that HolmesGPT executes itself, so
it works identically across the Anthropic API, Bedrock, OpenAI and any
LiteLLM-fronted gateway — no beta header, no server tools.

On large MCP deployments this keeps 10-60k tokens of tool schemas out of every turn
until they're actually needed.
"""

# Name of the meta-tool the model calls to discover/load held-back tools.
LOAD_TOOLS_NAME = "load_tools"

# OpenAI function-tool definition for load_tools. It is a normal function tool —
# HolmesGPT intercepts and executes it (see ToolCallingLLM._handle_load_tools).
LOAD_TOOLS_TOOL: dict = {
    "type": "function",
    "function": {
        "name": LOAD_TOOLS_NAME,
        "description": (
            "Search for and load additional tools that are not loaded by default. "
            "To keep the context small, heavy integrations are hidden until needed — "
            "for example: AWS, GitHub, PagerDuty, Kafka/MSK, Tempo tracing, "
            "OpenSearch/Elasticsearch metrics, and Grafana. Call this with keywords "
            "describing the capability you need (e.g. 'aws s3', 'github pull request', "
            "'kafka consumer lag', 'pagerduty incidents', 'tempo trace', "
            "'opensearch metrics'). The matching tools become available to call on your "
            "next step. If nothing matches, retry with broader keywords or an "
            "integration name. Tools already loaded in this conversation stay available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keywords (or a regular expression) describing the capability or "
                        "integration you need, e.g. 'aws ec2', 'github', 'kafka lag'."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}
