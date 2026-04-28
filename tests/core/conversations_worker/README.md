# Conversation Worker Tests

Tests for the M2 Conversation Worker live in two tiers: unit tests that mock
out external services, and integration tests that exercise a real Holmes
server against a real Supabase instance.

```
tests/core/conversations_worker/
├── test_dal_contract.py            # unit
├── test_event_publisher.py         # unit
├── test_realtime_manager.py        # unit
├── test_worker_edge_cases.py       # unit
├── test_worker_hydration.py        # unit
├── test_worker_lifecycle.py        # unit
├── test_worker_polling.py          # unit
└── integration/
    ├── __init__.py                 # SupabaseFixture helpers
    ├── conftest.py
    ├── test_conversation_integration.py
    └── broadcast_health_check.py   # standalone long-running monitor
```

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
and status transitions. They cover: single-turn, multi-turn, tool approval,
stop-conversation, error events, stress / concurrency, status lifecycle, and
rapid follow-ups.

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

To run a single test class or test:

```bash
poetry run pytest -k "TestSingleTurn" -m conversation_worker --no-cov -v
poetry run pytest -k "test_approval_pause_and_resume" -m conversation_worker --no-cov -v
```

### Key flags

- `-m conversation_worker` — selects only the integration tests (they are
  marked with `@pytest.mark.conversation_worker`).
- `--no-cov` — skip coverage; these are slow end-to-end tests.
- `-v` — verbose output.

### Test categories

- **TestSingleTurn** — simple question completes, answer has content,
  compaction works.
- **TestMultiTurn** — follow-up preserves history, compaction grows across
  turns.
- **TestToolApproval** — `approval_required` pause, approve and resume.
- **TestStopConversation** — stop mid-stream, verify `stopped` status.
- **TestErrorEvents** — successful conversation has no error event.
- **TestStress** — concurrent conversations queue and complete,
  `max_concurrent` is never exceeded.
- **TestStatusLifecycle** — `pending` → `running` → `completed` transitions.
- **TestRapidFollowups** — multiple fast follow-ups all complete without
  losing turns.

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
