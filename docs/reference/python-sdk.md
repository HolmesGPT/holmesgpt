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

**Example: a toolset that queries a custom incident management API.**

Create `my_incidents_toolset.py`:

```python
import logging
import requests
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, model_validator
from holmes.core.tools import Tool, Toolset, ToolsetTag, CallablePrerequisite

class IncidentAPIConfig(BaseModel):
    """Configuration for the incident management API."""
    model_config = ConfigDict(extra="allow")

    api_url: str
    api_token: str
    max_results: int = 25

    @model_validator(mode="after")
    def handle_deprecated_fields(self):
        extra = self.model_extra or {}
        if "base_url" in extra:
            self.api_url = extra["base_url"]
            logging.warning("Deprecated config name: base_url -> api_url")
        return self


class IncidentToolset(Toolset):
    """Toolset for querying an incident management API."""

    config_classes = [IncidentAPIConfig]

    def __init__(self, config: Optional[IncidentAPIConfig] = None):
        self._config = config or IncidentAPIConfig(
            api_url="", api_token=""
        )
        tools = [
            Tool(
                name="list_open_incidents",
                description="List all open incidents, optionally filtered by severity",
                func=self.list_incidents,
                parameters=[
                    {"name": "severity", "description": "Filter by severity: critical, high, medium, low", "type": "string"},
                ],
            ),
            Tool(
                name="get_incident_details",
                description="Get full details for a specific incident by ID",
                func=self.get_incident_details,
                parameters=[
                    {"name": "incident_id", "description": "The incident ID to look up", "type": "string"},
                ],
            ),
        ]

        super().__init__(
            name="incidents",
            description="Query the incident management system for open and recent incidents",
            enabled=True,
            tools=tools,
            tags=[ToolsetTag.CORE],
            prerequisites=[CallablePrerequisite(
                callable=self._check_connectivity,
                failure_message="Cannot reach incident API"
            )],
        )

    def _check_connectivity(self) -> bool:
        try:
            resp = requests.get(
                f"{self._config.api_url}/health",
                headers={"Authorization": f"Bearer {self._config.api_token}"},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def list_incidents(self, severity: str = "") -> str:
        params = {"status": "open", "limit": self._config.max_results}
        if severity:
            params["severity"] = severity
        resp = requests.get(
            f"{self._config.api_url}/api/v1/incidents",
            headers={"Authorization": f"Bearer {self._config.api_token}"},
            params=params,
            timeout=10,
        )
        if resp.status_code != 200:
            return f"Error querying incidents API: HTTP {resp.status_code} - {resp.text}"
        incidents = resp.json().get("incidents", [])
        if not incidents:
            return f"No open incidents found (filter: severity={severity or 'any'})"
        lines = []
        for inc in incidents:
            lines.append(f"[{inc['id']}] {inc['severity'].upper()} - {inc['title']} (assigned: {inc.get('assignee', 'unassigned')})")
        return "\n".join(lines)

    def get_incident_details(self, incident_id: str) -> str:
        resp = requests.get(
            f"{self._config.api_url}/api/v1/incidents/{incident_id}",
            headers={"Authorization": f"Bearer {self._config.api_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return f"Error fetching incident {incident_id}: HTTP {resp.status_code} - {resp.text}"
        return str(resp.json())
```

**Key patterns for Python toolsets:**

- Use a Pydantic `BaseModel` for configuration with `extra="allow"` to support backwards-compatible field renames
- Include a health check in the prerequisite callable
- Return detailed error messages including HTTP status codes and response bodies so the LLM can self-correct
- Use `requests` for HTTP calls (not specialized client libraries)
- Each tool function returns a string result

For more on the thin API wrapper pattern, see the built-in `servicenow_tables` toolset as a reference implementation.

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
