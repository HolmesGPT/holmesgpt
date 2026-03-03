# Install Python SDK

Embed HolmesGPT in your own applications for programmatic root cause analysis, based on observability data.

## Install HolmesGPT Python Package

```bash
pip install holmesgpt # Installs latest stable version
```

**Install unreleased version from GitHub:**

```bash
pip install "https://github.com/HolmesGPT/holmesgpt/archive/refs/heads/master.zip"
```

## Quick Start

```python
import os
from holmes.config import Config
from holmes.core.prompt import build_initial_ask_messages

config = Config(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    model="anthropic/claude-sonnet-4-5-20250929",
)

ai = config.create_console_toolcalling_llm()

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

For the full API reference, see the **[Python SDK Reference](../reference/python-sdk.md)**.
