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
)

# Create AI instance
ai = config.create_console_toolcalling_llm()

# Ask a question
messages = build_initial_ask_messages(
    initial_user_prompt="what pods are failing in production?",
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

## Loading Custom YAML Toolsets

Pass custom toolset file paths via the `custom_toolsets` parameter:

```python
config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
    custom_toolsets=["./my_toolset.yaml"],
)
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

For a complete reference on writing YAML toolsets, see [Custom Toolsets](../data-sources/custom-toolsets.md).

## Writing Custom Python Toolsets

For toolsets that need more than shell commands (e.g., API clients with authentication or response parsing), you can write Python-based toolsets and pass them to the SDK via `additional_toolsets`.

A Python toolset requires three things:

1. **A config class** (Pydantic `BaseModel`) - validates settings like API URLs and tokens
2. **Tool classes** (subclass `Tool`) - each tool implements `_invoke()` to do the actual work and `get_parameterized_one_liner()` for human-readable logging
3. **A toolset class** (subclass `Toolset`) - groups tools together and runs a health check via `prerequisites_callable()`

**Example: a toolset that calls httpbin.org to get the caller's IP address.**

```python
import os
import requests
from typing import Any, ClassVar, Dict, List, Tuple, Type
from pydantic import BaseModel
from holmes.config import Config
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetTag,
)


# 1. Config class
class HttpBinConfig(BaseModel):
    api_url: str = "https://httpbin.org"


# 2. Tool class - implements the actual HTTP call
class GetIp(Tool):
    def __init__(self, toolset: "HttpBinToolset"):
        super().__init__(
            name="httpbin_get_ip",
            description="Get the caller's public IP address via httpbin.org",
            parameters={},
        )
        # Important: set _toolset AFTER super().__init__() (Pydantic requirement)
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        resp = requests.get(f"{self._toolset.get_config().api_url}/ip", timeout=10)
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
        return "httpbin: Get IP address"


# 3. Toolset class - groups tools + health check
class HttpBinToolset(Toolset):
    config_classes: ClassVar[List[Type[HttpBinConfig]]] = [HttpBinConfig]

    def __init__(self):
        super().__init__(
            name="httpbin",
            description="Tools for testing HTTP requests via httpbin.org",
            enabled=True,
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[GetIp(self)],
            tags=[ToolsetTag.CORE],
        )
        self._httpbin_config = HttpBinConfig()

    def get_config(self) -> HttpBinConfig:
        return self._httpbin_config

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self._httpbin_config = HttpBinConfig(**config) if config else HttpBinConfig()
            resp = requests.get(f"{self._httpbin_config.api_url}/get", timeout=5)
            if resp.ok:
                return True, "httpbin.org reachable"
            return False, f"Health check failed: HTTP {resp.status_code}"
        except Exception as e:
            return False, f"Cannot reach httpbin.org: {e}"


# 4. Use it - pass the toolset via additional_toolsets
config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
    additional_toolsets=[HttpBinToolset()],
)

ai = config.create_console_toolcalling_llm()

messages = build_initial_ask_messages(
    initial_user_prompt="What is my public IP address?",
    file_paths=None,
    tool_executor=ai.tool_executor,
    runbooks=config.get_runbook_catalog(),
    system_prompt_additions=None,
)
response = ai.call(messages)
print(response.result)
```

**Key patterns:**

- Set `self._toolset` **after** `super().__init__()` in tool classes (Pydantic resets private attributes during init)
- `_invoke()` returns `StructuredToolResult` with `SUCCESS` or `ERROR` status
- Include detailed error messages (HTTP status + body) so the LLM can self-correct
- The health check in `prerequisites_callable()` validates config and checks connectivity
- Parameters use `Dict[str, ToolParameter]` (not a list)

See the built-in `servicenow_tables` toolset for a complete production example.

## API Reference

### `Config`

Main configuration class (`holmes.config.Config`).

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | Auto-detected | LLM API key. Reads from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. if not provided. |
| `model` | `str` | *None* | Model identifier, e.g. `"anthropic/claude-sonnet-4-5-20250929"`. Required. |
| `max_steps` | `int` | `40` | Maximum tool-calling steps per request. |
| `custom_toolsets` | `list[path]` | *None* | Paths to custom YAML toolset files. |
| `additional_toolsets` | `list[Toolset]` | *None* | Python `Toolset` instances to load alongside built-in toolsets. |
| `toolsets` | `dict` | *None* | Inline toolset configuration overrides. |

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `Config.load_from_file(config_file, **kwargs)` | `Config` | Load configuration from a YAML file. |
| `Config.load_from_env()` | `Config` | Load configuration from environment variables. |
| `create_console_toolcalling_llm()` | `ToolCallingLLM` | Create an AI instance for asking questions and investigating alerts. |
| `get_runbook_catalog()` | `RunbookCatalog` or `None` | Get the loaded runbook catalog. |

### `ToolCallingLLM`

Core AI engine for tool-calling interactions (`holmes.core.tool_calling_llm.ToolCallingLLM`).

| Method | Returns | Description |
|--------|---------|-------------|
| `call(messages)` | `LLMResult` | Run a tool-calling conversation with a full message list. |
| `prompt_call(system_prompt, user_prompt)` | `LLMResult` | Single-turn call with system and user prompts. |
| `messages_call(messages)` | `LLMResult` | Call with a message list (simpler signature than `call`). |

### `LLMResult`

Response object returned by `call()` and `prompt_call()`.

| Field | Type | Description |
|-------|------|-------------|
| `result` | `str` or `None` | The text response from the LLM. |
| `tool_calls` | `list[ToolCallResult]` or `None` | Tools that were called during the interaction. |
| `messages` | `list[dict]` or `None` | Full conversation history. Use for follow-up questions. |
| `num_llm_calls` | `int` or `None` | Number of LLM API round-trips. |
| `total_cost` | `float` | Total cost in USD. |
| `total_tokens` | `int` | Total tokens used. |
| `prompt_tokens` | `int` | Input tokens used. |
| `completion_tokens` | `int` | Output tokens used. |

### `ToolCallResult`

Represents a single tool invocation (`holmes.core.models.ToolCallResult`).

| Field | Type | Description |
|-------|------|-------------|
| `tool_call_id` | `str` | Unique identifier for this tool call. |
| `tool_name` | `str` | Name of the tool that was called. |
| `description` | `str` | Description of the tool. |
| `result` | `StructuredToolResult` | The tool's output. |

### `build_initial_ask_messages()`

Builds the initial message list (system prompt + user question) for an `ask` interaction (`holmes.core.prompt`).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `initial_user_prompt` | `str` | *required* | The question to ask. |
| `file_paths` | `list[Path]` or `None` | `None` | Optional file paths to include as context. |
| `tool_executor` | `ToolExecutor` | *required* | From `ai.tool_executor`. |
| `runbooks` | `RunbookCatalog` or `None` | `None` | From `config.get_runbook_catalog()`. |
| `system_prompt_additions` | `str` or `None` | `None` | Extra text appended to the system prompt. |

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
