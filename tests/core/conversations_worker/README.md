# Conversation Worker Tests

Tests for the M2 Conversation Worker live in `tests/core/conversations_worker/`:
unit tests directly under that folder, integration tests under
`tests/core/conversations_worker/integration/`.

## Unit tests (no external services)

These cover the worker's internal logic — hydration, edge cases, lifecycle,
polling, the DAL contract, the event publisher, the realtime manager — using
mocks. They need no running server, no Supabase, and no environment variables.

```bash
poetry run pytest tests/core/conversations_worker/ \
    -m "not conversation_worker and not llm" --no-cov -v
```

## Integration tests (require running Holmes + Supabase)

These tests create real `Conversations` rows in Supabase, wait for a running
Holmes server to process them, and assert on the resulting `ConversationEvents`
and status transitions.

### Prerequisites

1. A running Holmes server with the conversation worker enabled.
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

Wait until the server is fully up and the conversation worker has started its
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
