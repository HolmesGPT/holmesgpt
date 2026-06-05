#!/usr/bin/env python3
"""Generate ~30k log lines for the orders-api service over 1 hour, including a
single ERROR line that mentions user_id=USR_QXR42 (the needle) and ~80 prior
INFO lines for that same user across the hour (the history the parent must
later look up).

The eval is designed to require TWO log queries from the agent:

1. Find the ERROR — which mentions USR_QXR42.
2. Look up USR_QXR42's prior log history (login attempts, sessions) — which
   reveals the user had 3 failed logins in the 15 minutes before the ERROR.

Without a subagent, all 30k lines stay in the parent's context across both
queries. With a subagent, the parent only sees: "USR_QXR42 hit error X" +
the focused user-history result.

Usage:
    python generate_logs.py <loki_url>
"""

import json
import random
import sys
import urllib.request
from datetime import datetime, timedelta

random.seed(260)

NAMESPACE = "app-260"
POD_NAME = "orders-api-260-7f8c9d-pqr34"
SERVICE = "orders-api"
BATCH_SIZE = 200

# Incident timestamp — the ERROR mentioning the affected user fires here.
INCIDENT = datetime(2025, 9, 14, 11, 30, 0)
WINDOW_START = INCIDENT - timedelta(minutes=45)
WINDOW_END = INCIDENT + timedelta(minutes=15)

# The user we're tracking.
TARGET_USER = "USR_QXR42"

# Random other users for noise.
USERS = [f"USR_{random.randint(10000, 99999):05d}" for _ in range(200)]
ENDPOINTS = ["/checkout", "/cart", "/orders", "/items", "/search"]
STATUSES = ["200", "201", "204", "404", "500"]


def push(loki_url, streams_by_level):
    streams = []
    for level, values in streams_by_level.items():
        if not values:
            continue
        streams.append({
            "stream": {
                "job": "orders-api",
                "namespace": NAMESPACE,
                "pod_name": POD_NAME,
                "service": SERVICE,
                "level": level,
            },
            "values": values,
        })
    if not streams:
        return
    req = urllib.request.Request(
        loki_url.rstrip("/") + "/loki/api/v1/push",
        data=json.dumps({"streams": streams}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)


def ts(dt):
    return str(int(dt.timestamp() * 1_000_000_000))


def main():
    if len(sys.argv) < 2:
        print("usage: generate_logs.py <loki_url>", file=sys.stderr)
        sys.exit(1)
    loki_url = sys.argv[1]

    info, warn, error = [], [], []

    # Generate ~30k INFO log lines spread across the hour.
    seconds = int((WINDOW_END - WINDOW_START).total_seconds())
    # ~8 INFOs per second = ~28,800 over 60 min.
    for i in range(28_800):
        offset_s = (i * seconds) / 28_800
        t = WINDOW_START + timedelta(seconds=offset_s)
        user = random.choice(USERS)
        endpoint = random.choice(ENDPOINTS)
        status = random.choice(STATUSES)
        info.append([ts(t), f"INFO request user_id={user} endpoint={endpoint} status={status} latency_ms={random.randint(5, 250)}"])

    # The target user's history: 3 failed logins in the 15 minutes before the incident.
    for offset_min in (28, 22, 16):
        t = INCIDENT - timedelta(minutes=offset_min)
        warn.append([ts(t), f"WARN login_failed user_id={TARGET_USER} reason=invalid_password attempt={1 + (28 - offset_min) // 6}"])

    # A successful login for the target user 8 minutes before incident.
    t = INCIDENT - timedelta(minutes=8)
    info.append([ts(t), f"INFO login_success user_id={TARGET_USER} session_id=SESS_{random.randint(100000,999999)}"])

    # The incident ERROR — mentions the affected user.
    error.append([ts(INCIDENT), f"ERROR checkout_failed user_id={TARGET_USER} reason=payment_declined gateway_response=insufficient_funds order_id=ORD_99812"])

    # Push in batches.
    for level, lines in (("INFO", info), ("WARN", warn), ("ERROR", error)):
        for chunk_start in range(0, len(lines), BATCH_SIZE):
            chunk = lines[chunk_start:chunk_start + BATCH_SIZE]
            push(loki_url, {level: chunk})

    print(f"Pushed: {len(info)} INFO, {len(warn)} WARN, {len(error)} ERROR")


if __name__ == "__main__":
    main()
