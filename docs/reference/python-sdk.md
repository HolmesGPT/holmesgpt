# Python SDK Reference

Use the HolmesGPT Python SDK to embed AI-powered troubleshooting in your own applications.

## Quick Start

```python
import os
from holmes.config import Config
from holmes.core.prompt import build_initial_ask_messages

# Create configuration
config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
    max_steps=15,
)

# Create AI instance
ai = config.create_console_toolcalling_llm()

# Ask a question
question = "what pods are failing in production?"
messages = build_initial_ask_messages(
    initial_user_prompt=question,
    file_paths=None,
    tool_executor=ai.tool_executor,
    runbooks=config.get_runbook_catalog(),
    system_prompt_additions=None,
)

response = ai.call(messages)
print(response.result)
```

## Listing Available Tools

```python
import os
from holmes.config import Config

config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
)

ai = config.create_console_toolcalling_llm()

# List loaded toolsets and their status
for toolset in ai.tool_executor.toolsets:
    print(f"  {toolset.name} ({'enabled' if toolset.enabled else 'disabled'})")

# List individual tools
for tool_name in sorted(ai.tool_executor.tools_by_name.keys()):
    print(f"  {tool_name}")
```

## Inspecting Tool Calls

After each response, you can see which tools Holmes called:

```python
response = ai.call(messages)
print(response.result)

if response.tool_calls:
    for tc in response.tool_calls:
        print(f"Tool: {tc.tool_name}")
        print(f"Description: {tc.description}")
        print(f"Result: {tc.result}")
```

## Follow-up Questions

Maintain conversation context by reusing the message history returned in each response:

```python
import os
from holmes.config import Config
from holmes.core.prompt import build_initial_ask_messages

config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
)

ai = config.create_console_toolcalling_llm()

# First question - build initial messages with system prompt
messages = build_initial_ask_messages(
    initial_user_prompt="what pods are failing in my cluster?",
    file_paths=None,
    tool_executor=ai.tool_executor,
    runbooks=config.get_runbook_catalog(),
    system_prompt_additions=None,
)
response = ai.call(messages)
print(f"Holmes: {response.result}")

# Follow-up - append to the returned message history
messages = response.messages
messages.append({"role": "user", "content": "Can you show me the logs for those failing pods?"})
response = ai.call(messages)
print(f"Holmes: {response.result}")
```

## Loading Custom Toolsets

You can extend Holmes with your own YAML-based toolsets. Pass custom toolset file paths via the `custom_toolsets` parameter:

```python
import os
from holmes.config import Config
from holmes.core.prompt import build_initial_ask_messages

config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
    custom_toolsets=["./my_toolset.yaml"],
)

ai = config.create_console_toolcalling_llm()

messages = build_initial_ask_messages(
    initial_user_prompt="check the status of my custom service",
    file_paths=None,
    tool_executor=ai.tool_executor,
    runbooks=config.get_runbook_catalog(),
    system_prompt_additions=None,
)
response = ai.call(messages)
print(response.result)
```

**Example `my_toolset.yaml`:**

```yaml
toolsets:
  my-service-tools:
    description: "Tools for checking my custom service"
    prerequisites:
      - command: "curl --version"
    tools:
      - name: check_service_health
        description: "Check the health endpoint of my service"
        command: |
          curl -s "${MY_SERVICE_URL}/health"

      - name: get_service_metrics
        description: "Get Prometheus-style metrics from my service"
        command: |
          curl -s "${MY_SERVICE_URL}/metrics" | head -50
```

For a complete reference on writing custom toolsets, see [Custom Toolsets](../data-sources/custom-toolsets.md).

## Writing Custom Toolsets in Python

For toolsets that need more than shell commands (e.g., API clients with authentication, response parsing, or complex logic), you can write Python-based toolsets.

A Python toolset requires three things:

1. **A config class** (Pydantic `BaseModel`) - validates settings like API URLs and tokens
2. **Tool classes** (subclass `Tool`) - each tool implements `_invoke()` to do the actual work and `get_parameterized_one_liner()` for human-readable logging. Parameters are defined as `Dict[str, ToolParameter]`.
3. **A toolset class** (subclass `Toolset`) - groups tools together and runs a health check via `prerequisites_callable()`

**Example: a toolset that queries a service's REST API.**

```python
import requests
from typing import Any, ClassVar, Dict, List, Tuple, Type
from pydantic import BaseModel
from holmes.core.tools import (
    CallablePrerequisite,
    Tool,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.core.models import StructuredToolResult, StructuredToolResultStatus, ToolInvokeContext


# 1. Config: validated by Pydantic when the toolset loads
class MyServiceConfig(BaseModel):
    api_url: str
    api_token: str


# 2. Tool: one class per API endpoint
class GetServiceStatus(Tool):
    _toolset: "MyServiceToolset"

    def __init__(self, toolset: "MyServiceToolset"):
        self._toolset = toolset
        super().__init__(
            name="get_service_status",
            description="Get the current status and version of the service",
            parameters={},  # no parameters needed
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        config = self._toolset.config
        resp = requests.get(
            f"{config.api_url}/api/v1/status",
            headers={"Authorization": f"Bearer {config.api_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=f"HTTP {resp.status_code}: {resp.text}",
                params=params,
            )
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=resp.json(),
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get service status"


class SearchRecords(Tool):
    _toolset: "MyServiceToolset"

    def __init__(self, toolset: "MyServiceToolset"):
        self._toolset = toolset
        super().__init__(
            name="search_records",
            description="Search records by query string",
            parameters={
                "query": ToolParameter(description="Search query", type="string"),
                "limit": ToolParameter(description="Max results (default 10)", type="integer", required=False),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        config = self._toolset.config
        resp = requests.get(
            f"{config.api_url}/api/v1/records",
            headers={"Authorization": f"Bearer {config.api_token}"},
            params={"q": params["query"], "limit": params.get("limit", 10)},
            timeout=10,
        )
        if resp.status_code != 200:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=f"HTTP {resp.status_code}: {resp.text}",
                params=params,
            )
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=resp.json(),
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search '{params.get('query', '')}'"


# 3. Toolset: groups tools + runs health check
class MyServiceToolset(Toolset):
    config_classes: ClassVar[List[Type[MyServiceConfig]]] = [MyServiceConfig]

    def __init__(self):
        super().__init__(
            name="my-service",
            description="Query my service's REST API",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[GetServiceStatus(self), SearchRecords(self)],
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = MyServiceConfig(**config)
            resp = requests.get(
                f"{self.config.api_url}/health",
                headers={"Authorization": f"Bearer {self.config.api_token}"},
                timeout=5,
            )
            if resp.ok:
                return True, "Connected to service"
            return False, f"Health check failed: HTTP {resp.status_code}"
        except Exception as e:
            return False, f"Cannot reach service: {e}"
```

**Key patterns:**

- `_invoke()` returns `StructuredToolResult` with `SUCCESS` or `ERROR` status
- Include detailed error messages (HTTP status + body) so the LLM can self-correct
- Use `requests` for HTTP calls (not specialized client libraries)
- The health check in `prerequisites_callable()` validates config and checks connectivity
- Parameters use `Dict[str, ToolParameter]` (not a list)

See the built-in `servicenow_tables` toolset for a complete production example.

## API Reference

### Config

Main configuration class (`holmes.config.Config`).

**Constructor parameters:**

- `api_key` (str, optional) - LLM API key. Auto-detected from environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) if not provided.
- `model` (str, optional) - Model to use (e.g., `"anthropic/claude-sonnet-4-5-20250929"`). No default; must be set via constructor, environment, or config file.
- `max_steps` (int) - Maximum tool-calling steps per request. Default: `40`.
- `custom_toolsets` (list of file paths, optional) - Paths to custom toolset YAML files.
- `toolsets` (dict, optional) - Inline toolset configuration overrides.

**Class methods:**

- `Config.load_from_file(config_file, **kwargs)` - Load configuration from a YAML file.
- `Config.load_from_env()` - Load configuration from environment variables.

**Instance methods:**

- `create_console_toolcalling_llm()` - Create a `ToolCallingLLM` instance for asking questions.
- `create_console_issue_investigator()` - Create an `IssueInvestigator` instance for investigating alerts.
- `get_runbook_catalog()` - Get the loaded runbook catalog (or `None`).

### ToolCallingLLM

Core AI engine for tool-calling interactions (`holmes.core.tool_calling_llm.ToolCallingLLM`).

**Methods:**

- `call(messages)` - Run a tool-calling conversation with a full message list. Returns `LLMResult`.
- `prompt_call(system_prompt, user_prompt)` - Single-turn call with system and user prompts. Returns `LLMResult`.
- `messages_call(messages)` - Call with a message list (alias with fewer options). Returns `LLMResult`.

### LLMResult

Response object returned by `call()` and `prompt_call()` (`holmes.core.tool_calling_llm.LLMResult`).

**Fields:**

- `result` (str, optional) - The text response from the LLM.
- `tool_calls` (list of `ToolCallResult`, optional) - Tools that were called during the interaction.
- `messages` (list of dict, optional) - Full conversation history including the response. Use this for follow-up questions.
- `num_llm_calls` (int, optional) - Number of LLM API round-trips.
- `total_cost` (float) - Total cost in USD.
- `total_tokens` (int) - Total tokens used.
- `prompt_tokens` (int) - Input tokens used.
- `completion_tokens` (int) - Output tokens used.

### ToolCallResult

Represents a single tool invocation (`holmes.core.models.ToolCallResult`).

**Fields:**

- `tool_call_id` (str) - Unique identifier for this tool call.
- `tool_name` (str) - Name of the tool that was called.
- `description` (str) - Description of the tool.
- `result` (StructuredToolResult) - The tool's output.

### Helper Functions

**`build_initial_ask_messages()`** (`holmes.core.prompt`):

Builds the initial message list (system prompt + user question) for an `ask` interaction.

```python
from holmes.core.prompt import build_initial_ask_messages

messages = build_initial_ask_messages(
    initial_user_prompt="your question here",
    file_paths=None,                    # Optional list of file paths to include
    tool_executor=ai.tool_executor,     # From the ToolCallingLLM instance
    runbooks=config.get_runbook_catalog(),  # Optional runbook catalog
    system_prompt_additions=None,       # Optional extra system prompt text
)
```

## Environment Variables

Instead of passing `api_key` to the Config constructor, you can set environment variables:

```bash
# AI Provider (choose one)
export ANTHROPIC_API_KEY="your-anthropic-key"
export OPENAI_API_KEY="your-openai-key"

# Optional
export HOLMES_CONFIG_PATH="/path/to/config.yaml"
export HOLMES_LOG_LEVEL="INFO"
```

See the [Environment Variables Reference](environment-variables.md) for complete documentation.

## Next Steps

- **[Custom Toolsets](../data-sources/custom-toolsets.md)** - Full reference for writing YAML toolsets
- **[Recommended Setup](../data-sources/recommended-setup.md)** - Connect metrics, logs, and cloud providers
- **[All Data Sources](../data-sources/index.md)** - Browse 38+ built-in integrations
