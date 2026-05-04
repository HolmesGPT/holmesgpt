"""Long-running broadcast health check.

Creates a conversation every 15 minutes, sends a broadcast, and measures
how long it takes Holmes to claim it. If broadcast is working, claim
happens in <10s. If it falls back to poll, claim takes ~120s.

Run:
    ENABLE_CONVERSATION_WORKER=true CONVERSATION_WORKER_USE_REALTIME_BROADCAST=true \
        poetry run python server.py &
    poetry run python tests/core/conversations_worker/integration/broadcast_health_check.py

Requires: ROBUSTA_UI_TOKEN, CLUSTER_NAME
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from realtime._async.client import AsyncRealtimeClient
from supabase import create_client
from supabase.lib.client_options import ClientOptions

from holmes.core.conversations_worker.realtime_manager import broadcast_submit_topic

_log_file = os.environ.get("BROADCAST_HEALTH_LOG", "/tmp/broadcast-health.log")
_fh = logging.FileHandler(_log_file, mode="w")
_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh], force=True)
log = logging.getLogger("broadcast-health")

# ---- config ----
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", 15))
TOTAL_DURATION_HOURS = float(os.environ.get("TOTAL_DURATION_HOURS", 2))
BROADCAST_CLAIM_THRESHOLD_SECONDS = 30  # claim within this = broadcast works

# ---- setup ----
raw = os.environ.get("ROBUSTA_UI_TOKEN")
if not raw:
    sys.exit("ROBUSTA_UI_TOKEN not set")
decoded = json.loads(base64.b64decode(raw))
cluster_id = os.environ.get("CLUSTER_NAME")
if not cluster_id:
    sys.exit("CLUSTER_NAME not set")

options = ClientOptions(postgrest_client_timeout=60)
client = create_client(decoded["store_url"], decoded["api_key"], options)
res = client.auth.sign_in_with_password(
    {"email": decoded["email"], "password": decoded["password"]}
)
client.auth.set_session(res.session.access_token, res.session.refresh_token)
client.postgrest.auth(res.session.access_token)
account_id = decoded["account_id"]
user_id = res.user.id

# ---- persistent broadcast connection ----
broadcast_ch = None
broadcast_loop = None


def _setup_broadcast():
    global broadcast_ch, broadcast_loop
    loop = asyncio.new_event_loop()
    broadcast_loop = loop

    async def _setup():
        global broadcast_ch
        store_url = decoded["store_url"].rstrip("/")
        if store_url.startswith("https://"):
            ws_url = "wss://" + store_url[len("https://"):]
        else:
            ws_url = "ws://" + store_url[len("http://"):]
        ws_url = f"{ws_url}/realtime/v1"

        topic = broadcast_submit_topic(account_id, cluster_id)
        rt = AsyncRealtimeClient(url=ws_url, token=decoded["api_key"], auto_reconnect=True)
        await rt.connect()
        ch = rt.channel(topic, {"config": {"private": False}})
        subscribed = asyncio.Event()

        def _on_sub(status, err=None):
            if "SUBSCRIBED" in str(status).upper():
                subscribed.set()

        await ch.subscribe(_on_sub)
        await asyncio.wait_for(subscribed.wait(), timeout=10)
        broadcast_ch = ch
        log.info("Broadcast sender connected on topic=%s", topic)
        while True:
            await asyncio.sleep(1)

    try:
        loop.run_until_complete(_setup())
    except BaseException as e:
        log.error("Broadcast sender setup failed: %s", e)


t = threading.Thread(target=_setup_broadcast, daemon=True)
t.start()
time.sleep(5)
if broadcast_ch is None:
    sys.exit("Failed to set up broadcast channel")


def send_broadcast(conversation_id: str):
    future = asyncio.run_coroutine_threadsafe(
        broadcast_ch.send_broadcast("pending_conversations", {"conversation_id": conversation_id}),
        broadcast_loop,
    )
    future.result(timeout=5)


# ---- test helpers ----
created_ids = []


def create_conversation(label: str) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    conv = client.rpc(
        "post_new_conversation",
        {
            "_account_id": account_id,
            "_cluster_id": cluster_id,
            "_origin": "chat",
            "_user_id": user_id,
            "_title": f"broadcast-health: {label}",
            "_initial_events": [
                {"event": "user_message", "data": {"ask": "Say ok."}, "ts": now_iso}
            ],
        },
    ).execute().data
    created_ids.append(conv["conversation_id"])
    return conv


def wait_for_claim(conversation_id: str, timeout: float = 150) -> float:
    """Wait until conversation leaves 'pending' status. Return seconds elapsed."""
    start = time.time()
    while time.time() - start < timeout:
        conv = client.table("Conversations").select("status").eq(
            "conversation_id", conversation_id
        ).single().execute().data
        if conv["status"] != "pending":
            return time.time() - start
        time.sleep(0.5)
    return timeout


def cleanup():
    for cid in created_ids:
        try:
            conv = client.table("Conversations").select("status").eq(
                "conversation_id", cid
            ).single().execute().data
            if conv["status"] in ("pending", "queued", "running"):
                client.rpc("stop_conversation", {
                    "_conversation_id": cid, "_account_id": account_id
                }).execute()
        except Exception:
            pass
        try:
            client.table("ConversationEvents").delete().eq("conversation_id", cid).execute()
            client.table("Conversations").delete().eq("conversation_id", cid).execute()
        except Exception:
            pass


# ---- main loop ----
signal.signal(signal.SIGINT, lambda *_: (cleanup(), sys.exit(0)))
signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(0)))

total_checks = int(TOTAL_DURATION_HOURS * 60 / CHECK_INTERVAL_MINUTES)
results = []

log.info(
    "Starting broadcast health check: %d checks over %.1f hours (every %d min)",
    total_checks, TOTAL_DURATION_HOURS, CHECK_INTERVAL_MINUTES,
)

for i in range(1, total_checks + 1):
    elapsed_min = (i - 1) * CHECK_INTERVAL_MINUTES
    label = f"check-{i} (t+{elapsed_min}min)"
    log.info("--- %s ---", label)

    conv = create_conversation(label)
    cid = conv["conversation_id"]
    log.info("Created conversation %s", cid)

    send_broadcast(cid)
    log.info("Broadcast sent")

    claim_time = wait_for_claim(cid)
    via_broadcast = claim_time < BROADCAST_CLAIM_THRESHOLD_SECONDS
    status = "BROADCAST" if via_broadcast else "POLL-FALLBACK"
    results.append((label, claim_time, status))

    log.info(
        "Claimed in %.1fs → %s%s",
        claim_time,
        status,
        "" if via_broadcast else " *** BROADCAST MAY BE STALE ***",
    )

    if i < total_checks:
        log.info("Sleeping %d min until next check...", CHECK_INTERVAL_MINUTES)
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# ---- report ----
log.info("\n=== BROADCAST HEALTH REPORT ===")
all_ok = True
for label, claim_time, status in results:
    marker = "✓" if status == "BROADCAST" else "✗"
    log.info("  %s %s: %.1fs (%s)", marker, label, claim_time, status)
    if status != "BROADCAST":
        all_ok = False

if all_ok:
    log.info("RESULT: All %d checks received broadcast within %ds", len(results), BROADCAST_CLAIM_THRESHOLD_SECONDS)
else:
    failures = sum(1 for _, _, s in results if s != "BROADCAST")
    log.info("RESULT: %d/%d checks fell back to polling — broadcast subscription degraded", failures, len(results))

cleanup()
