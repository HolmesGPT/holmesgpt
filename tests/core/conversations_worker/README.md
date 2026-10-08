# Conversation Worker Tests

Tests for the M2 Conversation Worker live in `tests/core/conversations_worker/`:
unit tests directly under that folder, integration tests under
`tests/core/conversations_worker/integration/`.

## Unit tests (no external services)

These cover the runtime's internal logic using mocks, one file per module:
`test_models.py` (the executor-name rule, `ConversationTask.from_row`, row identity),
`test_sizing.py` (executor sizes: the account setting validated once, built-ins, ceiling),
`test_executor.py` (one executor's claim loop, dispatch, slots, saturation logging),
`test_registry.py` (executors on demand, the cap, live resizing, discovery),
`test_processor.py` plus `test_processor_hydration.py` / `test_processor_edge_cases.py` /
`test_processor_frontend_tools.py` / `test_processor_usage_recorder.py` (one row → one
turn, and the outcome writes), `test_runtime_lifecycle.py` / `test_runtime_polling.py`
(the runtime: Realtime verification, start/stop, the shutdown sweep), the DAL contract,
the event publisher and the realtime manager. They need no running server, no Supabase,
and no environment variables.

```bash
poetry run pytest tests/core/conversations_worker/ \
    -m "not conversation_worker and not llm" --no-cov -v
```

## Integration tests (require running Holmes + Supabase)

These tests create real `Conversations` rows in Supabase, wait for a running
Holmes server to process them, and assert on the resulting `ConversationEvents`
and status transitions.

### Prerequisites

1. A running Holmes server with the conversation runtime enabled (`ENABLE_CONVERSATION_WORKER`).
2. Two environment variables:
   - `ROBUSTA_UI_TOKEN` — base64-encoded JSON containing:
     ```json
     {
       "store_url": "...",
       "api_key": "...",
       "email": "...",
       "password": "...",
       "account_id": "..."
     }
     ```
   - `CLUSTER_NAME` — cluster name that matches the Holmes server's config.

### Step 1: Start the Holmes server

In a separate terminal (or background):

```bash
ENABLE_CONVERSATION_WORKER=true \
CONVERSATION_WORKER_USE_REALTIME_BROADCAST=true \
ROBUSTA_UI_TOKEN="<your-token>" \
CLUSTER_NAME="<your-cluster>" \
poetry run python server.py
```

Wait until the server is fully up and the conversation runtime has started its
claim loop.

### Step 2: Run the integration tests

In another terminal (with the same env vars exported):

```bash
ROBUSTA_UI_TOKEN="<your-token>" \
CLUSTER_NAME="<your-cluster>" \
poetry run pytest tests/core/conversations_worker/integration/ \
    -m conversation_worker --no-cov -v
```

To list every test or test class without running them:

```bash
poetry run pytest tests/core/conversations_worker/integration/ \
    -m conversation_worker --no-cov --collect-only -q
```

To run a single test class or test, pass its name to `-k`:

```bash
poetry run pytest -k "<TestClass>" -m conversation_worker --no-cov -v
poetry run pytest -k "<test_name>" -m conversation_worker --no-cov -v
```

### Key flags

- `-m conversation_worker` — selects only the integration tests (they are
  marked with `@pytest.mark.conversation_worker`).
- `--no-cov` — skip coverage; these are slow end-to-end tests.
- `-v` — verbose output.

### Timeouts

Individual tests wait up to 120s per turn (LLM response time). The stress
tests wait up to 300s total. If your LLM is slow, you may need to adjust.

### Cleanup

The fixture automatically stops and deletes all conversations it created
during teardown (session-scoped). If tests crash, leftover rows in Supabase's
`Conversations` / `ConversationEvents` tables can be cleaned manually.

## Broadcast health check (optional)

There's also a standalone broadcast health-check script that runs for hours,
creating a conversation every N minutes and measuring claim latency:

```bash
poetry run python tests/core/conversations_worker/integration/broadcast_health_check.py
```

It requires the same env vars, plus `ENABLE_CONVERSATION_WORKER` and
`CONVERSATION_WORKER_USE_REALTIME_BROADCAST` set on the Holmes server.
