# Run on Dapr Workflow (3&#8203;rd party)

A long investigation is vulnerable to a restart. If the process or its node goes
away halfway through, the work done so far is lost and the investigation starts
again from the first tool call.

This third-party integration from Diagrid runs HolmesGPT on
[Dapr Workflow](https://docs.dapr.io/developing-applications/building-blocks/workflow/workflow-overview/),
a durable execution engine. Each LLM iteration and each tool call runs as a
workflow activity, which gives you:

-   **Resume after a restart** — an investigation continues from the last
    completed activity instead of starting over.
-   **Durable approvals** — a tool call that sets `requires_approval` becomes a
    durable wait. It can sit for days without holding a process open.
-   **Reconnectable streams** — the event tape is kept in a Dapr state store, so
    a client can reconnect with `Last-Event-Id` and pick up where it left off.

HolmesGPT is consumed as a library, so nothing in HolmesGPT itself has to
change.

## Prerequisites

```bash
pip install "diagrid[holmesgpt]"
```

Install this in a dedicated environment. The `holmesgpt` extra is deliberately
not part of `diagrid[all]`, because HolmesGPT pins `fastapi`, `uvicorn`,
`cachetools`, `mcp` and `httpx` more strictly than the other integrations do.

You also need a Dapr sidecar. `dapr init` creates its supporting containers,
so install Docker and have the daemon running first. If you cannot run Docker,
see Dapr's
[self-hosted initialization options](https://docs.dapr.io/getting-started/install-dapr-selfhost/).

```bash
brew install dapr/tap/dapr-cli
dapr init
```

`dapr init` on its own is enough. The integration writes its event tape to the
same state store the workflow runtime already uses for actor state — the
default `statestore` component that `dapr init` creates. There is no second
component to configure.

That default is Redis, but any Dapr-supported actor state store works
(PostgreSQL, MongoDB, MySQL, SQLite, Cosmos DB, etcd and others). The
integration only uses the portable state API.

## Quick start

```python
from pathlib import Path

from diagrid.agent.holmesgpt import DaprWorkflowHolmesRunner

runner = DaprWorkflowHolmesRunner(
    name="sre-agent",
    config_path=Path("~/.holmes/config.yaml").expanduser(),  # optional
    model="anthropic/claude-sonnet-4-5-20250929",  # optional
    max_steps=10,
)
runner.start()

result = runner.ask("Why is checkout-api crashlooping?")
print(result["final"]["content"])

runner.shutdown()
```

`ask()` takes the same shape as `holmes ask "<question>"`. The system prompt,
toolset instructions, skills and global instructions are rendered exactly as the
CLI and HTTP server render them, because `ask()` delegates to
`build_chat_messages`. For lower-level control over pre-rendered messages, use
`runner.invoke(messages=...)` instead.

Save that example as `basic.py`, then run it with a sidecar:

```bash
dapr run --app-id holmes-cli -- python basic.py
```

## Learn more

-   [Integration README and examples](https://github.com/diagridio/python-ai/tree/main/diagrid/agent/holmesgpt)
-   [Diagrid documentation for HolmesGPT](https://docs.diagrid.io/develop/agents/holmesgpt/)
-   [Running it on Kubernetes](https://github.com/diagrid-labs/diagrid-holmesgpt)

## Support and licensing

This integration is built and maintained by Diagrid, not by the HolmesGPT
maintainers. Report problems with it on
[diagridio/python-ai](https://github.com/diagridio/python-ai/issues).

The `diagrid` package is distributed under the Business Source License 1.1, with
a change date of 1 March 2030 after which it converts to Apache 2.0. Dapr itself
is a CNCF-graduated project under Apache 2.0 and can be used on its own.
