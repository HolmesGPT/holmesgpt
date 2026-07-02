"""Integration test: a conversation completes end-to-end even when the worker's
Supabase RPCs hit transient infrastructure errors.

Unlike the other tests in this directory (which assume an *external* Holmes
server processes the conversation), this one runs the worker **in-process** so
it can wrap the worker's Supabase client and inject simulated transient
failures (503s) into every conversation RPC. The bounded retry policy added to
``SupabaseDal`` must absorb them and still drive the conversation to
``completed``.

Requires a Robusta token + cluster and uses the Robusta LLM endpoint:
    ROBUSTA_UI_TOKEN   - base64 JSON with Supabase credentials + account_id
    CLUSTER_NAME       - target cluster
    ROBUSTA_API_ENDPOINT - Holmes uses it to reach the LLM

Run:
    poetry run pytest tests/core/conversations_worker/integration/test_retry_resilience.py \
        -m conversation_worker --no-cov -v
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Dict, Optional

import pytest

from tests.core.conversations_worker.integration import SupabaseFixture

pytestmark = [pytest.mark.conversation_worker, pytest.mark.integration]

# The conversation RPCs whose transient errors the retry policy must absorb.
_CONVERSATION_RPCS = {
    "claim_n_pending_conversations",
    "get_conversation_events",
    "post_conversation_events",
    "update_conversation_status",
}


@pytest.fixture(scope="module")
def inprocess_worker():
    """Import the fully-wired Holmes server (config, dal, chat, worker) and run
    the conversation worker in this process. Heavy (~30s) — module scoped."""
    if not os.environ.get("ROBUSTA_UI_TOKEN"):
        pytest.skip("ROBUSTA_UI_TOKEN not set")

    from holmes.core.supabase_dal import SupabaseDal

    import server  # builds config, dal, chat, conversation_worker at import

    # In a combined session a unit test may have activated the root
    # session-scoped ``storage_dal_mock`` (patches holmes.config.SupabaseDal)
    # before this dir's override took effect, leaving server.dal a MagicMock.
    # Run this test in isolation with ``-m conversation_worker``.
    if not isinstance(server.dal, SupabaseDal):
        pytest.skip(
            "SupabaseDal is mocked — run in isolation: "
            "pytest tests/core/conversations_worker/integration -m conversation_worker"
        )
    if server.conversation_worker is None:
        pytest.skip("conversation worker not enabled (ENABLE_CONVERSATION_WORKER)")
    if not server.dal.enabled:
        pytest.skip("Supabase DAL not enabled")
    server.dal.sign_in()
    return server


class _TransientFaultInjector:
    """Wrap a Supabase client's ``rpc().execute()`` so it raises a simulated
    transient error with probability ``p`` — but never more than ``max_consec``
    times in a row, so the bounded (3-attempt) retry always recovers."""

    def __init__(self, client: Any, p: float = 0.6, max_consec: int = 2, seed: int = 7):
        self._client = client
        self._real_rpc = client.rpc
        self._rng = random.Random(seed)
        self.p = p
        self.max_consec = max_consec
        self.on = False
        self.consec = 0
        self.injected = 0
        self.by_rpc: Dict[str, int] = {}

    def install(self) -> "_TransientFaultInjector":
        inj = self
        real_rpc = self._real_rpc

        def flaky_rpc(name, params=None):
            builder = real_rpc(name, params) if params is not None else real_rpc(name)
            real_execute = builder.execute

            def execute(*a, **k):
                if (
                    inj.on
                    and inj.consec < inj.max_consec
                    and inj._rng.random() < inj.p
                ):
                    inj.consec += 1
                    inj.injected += 1
                    inj.by_rpc[name] = inj.by_rpc.get(name, 0) + 1
                    raise Exception(
                        "503 Service Unavailable (simulated transient Supabase error)"
                    )
                inj.consec = 0
                return real_execute(*a, **k)

            builder.execute = execute
            return builder

        self._client.rpc = flaky_rpc
        return self

    def restore(self) -> None:
        self._client.rpc = self._real_rpc


def test_conversation_completes_through_transient_supabase_failures(
    inprocess_worker, supabase_fx: SupabaseFixture
):
    worker = inprocess_worker.conversation_worker
    dal = inprocess_worker.dal

    injector = _TransientFaultInjector(dal.client).install()
    # We drive the worker manually, so skip the broadcast notify on create
    # (it would otherwise need a live Realtime WS that nothing listens on here).
    prev_pgchanges = supabase_fx.use_pgchanges
    supabase_fx.use_pgchanges = True
    try:
        conv = supabase_fx.create_conversation(
            ask="Reply with exactly the single word PONG and nothing else.",
            title="retry-resilience-e2e",
        )
        cid = conv["conversation_id"]

        # Faults on from here: every worker RPC may hit a transient 503.
        injector.on = True

        # Poll the claim like the real worker's poll loop — the just-created
        # row may not be visible to the very first claim (create→claim race).
        mine: list = []
        deadline = time.time() + 20
        while time.time() < deadline:
            # Claim just one row (the one under test) — the claim lands it
            # directly in 'running', so a larger limit would strand unrelated
            # pending rows in 'running' without dispatch.
            claimed = worker.dal.claim_n_pending_conversations(worker.holmes_id, 1)
            mine = [c for c in claimed if c["conversation_id"] == cid]
            if mine:
                break
            time.sleep(1)
        assert mine, "worker did not claim the created conversation within 20s"
        task = worker._build_task_from_conversation_row(mine[0])

        worker._process_conversation(task)

        injector.on = False

        final = supabase_fx.wait_for_terminal(cid, request_sequence=1, timeout=120)
        term: Optional[Dict[str, Any]] = supabase_fx.find_terminal_event(cid)
        events_blob = json.dumps(supabase_fx.get_events(cid))

        assert final["status"] == "completed", f"unexpected status: {final}"
        assert term and term.get("event") == "ai_answer_end"
        assert "PONG" in events_blob.upper(), "LLM answer not persisted"

        # The point of the test: transient failures were actually injected and
        # absorbed, and only on the conversation RPCs.
        assert injector.injected > 0, "no transient failures were injected"
        assert set(injector.by_rpc) <= _CONVERSATION_RPCS, injector.by_rpc
    finally:
        injector.restore()
        supabase_fx.use_pgchanges = prev_pgchanges
        # supabase_fx session teardown stops + deletes created conversations.
