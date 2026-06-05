#!/usr/bin/env python3
"""Generate ~30k log lines for the inventory-api service over 1 hour with TWO
distinct anomalies that the user prompt asks about in sequence — so a
persistent subagent (Option D) that retains the fetched dataset for a
follow-up should win vs. one that re-fetches.

Anomaly A: at 14:15 UTC, a single CRITICAL line says the warehouse
connection to ZONE_EU3 failed.
Anomaly B: in the 20 minutes BEFORE 14:15, there are 47 retry attempts
to ZONE_EU3 (WARN level) that the user only asks about in the follow-up
clause of the prompt.

Usage: python generate_logs.py <loki_url>
"""

import json
import random
import sys
import urllib.request
from datetime import datetime, timedelta

random.seed(261)

NAMESPACE = "app-261"
POD_NAME = "inventory-api-261-3c7d8e-mnq67"
SERVICE = "inventory-api"
BATCH_SIZE = 200

INCIDENT = datetime(2025, 10, 21, 14, 15, 0)
WINDOW_START = INCIDENT - timedelta(minutes=45)
WINDOW_END = INCIDENT + timedelta(minutes=15)


def push(loki_url, streams_by_level):
    streams = []
    for level, values in streams_by_level.items():
        if not values:
            continue
        streams.append({
            "stream": {
                "job": "inventory-api",
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

    info, warn, error, critical = [], [], [], []

    ZONES = ["ZONE_US1", "ZONE_US2", "ZONE_EU1", "ZONE_EU2", "ZONE_EU3", "ZONE_AS1"]
    SKUS = [f"SKU_{random.randint(100000, 999999)}" for _ in range(500)]

    seconds = int((WINDOW_END - WINDOW_START).total_seconds())
    for i in range(28_800):
        offset_s = (i * seconds) / 28_800
        t = WINDOW_START + timedelta(seconds=offset_s)
        zone = random.choice(ZONES)
        sku = random.choice(SKUS)
        action = random.choice(["fetch", "reserve", "release", "lookup"])
        info.append([ts(t), f"INFO inventory_{action} zone={zone} sku={sku} qty={random.randint(1, 50)} latency_ms={random.randint(5, 180)}"])

    # 47 retry attempts to ZONE_EU3 (this is the follow-up anomaly).
    for k in range(47):
        offset_s = random.uniform(60, 20 * 60)  # within 20 min before incident
        t = INCIDENT - timedelta(seconds=offset_s)
        warn.append([ts(t), f"WARN warehouse_retry zone=ZONE_EU3 attempt={k+1} backoff_ms={50 * (k+1)}"])

    # The CRITICAL incident line.
    critical.append([ts(INCIDENT), "CRITICAL warehouse_connection_lost zone=ZONE_EU3 reason=tcp_reset peer_addr=10.42.7.18:9300 last_successful_sync=2025-10-21T14:09:43Z"])

    for level, lines in (("INFO", info), ("WARN", warn), ("CRITICAL", critical)):
        for chunk_start in range(0, len(lines), BATCH_SIZE):
            chunk = lines[chunk_start:chunk_start + BATCH_SIZE]
            push(loki_url, {level: chunk})

    print(f"Pushed: {len(info)} INFO, {len(warn)} WARN, {len(critical)} CRITICAL")


if __name__ == "__main__":
    main()
