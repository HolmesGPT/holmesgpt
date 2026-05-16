#!/usr/bin/env python3
"""Generate the same historical log timeline as 101_loki_historical_logs_pod_deleted
and push it directly to a local Loki instance (no Promtail, no Kubernetes).

Usage:
    python generate_logs.py <loki_url>

Pushes logs to <loki_url>/loki/api/v1/push with the same labels Holmes expects.
"""

import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

random.seed(100)

NAMESPACE = "app-259"
POD_NAME = "payment-api-259-d8f7b9c4-abc12"
SERVICE = "payment-api"
BATCH_SIZE = 200


def push(loki_url, streams_by_level):
    """Push grouped streams to Loki."""
    streams = []
    for level, values in streams_by_level.items():
        if not values:
            continue
        streams.append(
            {
                "stream": {
                    "job": "payment-api",
                    "namespace": NAMESPACE,
                    "pod_name": POD_NAME,
                    "service": SERVICE,
                    "level": level,
                },
                "values": values,
            }
        )
    if not streams:
        return
    payload = {"streams": streams}
    req = urllib.request.Request(
        loki_url + "/loki/api/v1/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"Loki push failed {e.code}: {body}")


def log_entry(level, message, **extra):
    return {
        "level": level,
        "message": message,
        "service": SERVICE,
        "pod": POD_NAME,
        **extra,
    }


def generate(loki_url):
    """Generate the same logs as 101 (and 100a's incident period), push in batches."""
    problem_start = datetime(2025, 8, 2, 13, 45, 0)
    problem_end = datetime(2025, 8, 2, 14, 45, 0)

    current = datetime(2025, 8, 1, 12, 0, 0)
    scenario_end = datetime(2025, 8, 4, 14, 0, 0)

    streams_by_level: dict[str, list[list[str]]] = {}
    total = 0

    def add(ts: datetime, level: str, data: dict):
        ts_nano = str(int(ts.timestamp() * 1e9))
        streams_by_level.setdefault(level, []).append([ts_nano, json.dumps(data)])

    while current < scenario_end:
        if random.random() < 0.05:
            add(
                current,
                "INFO",
                log_entry(
                    "INFO",
                    "Payment processed successfully",
                    payment_id=f"PAY-{random.randint(1000, 9999)}",
                ),
            )
            total += 1

        if problem_start <= current <= problem_end:
            if random.random() < 0.4:
                add(
                    current,
                    "ERROR",
                    log_entry(
                        "ERROR",
                        "Failed to acquire database connection - pool exhausted",
                        wait_time_ms=random.randint(1000, 5000),
                        queue_length=random.randint(5, 15),
                    ),
                )
                total += 1

        current += timedelta(minutes=random.randint(1, 5))

        # Flush in batches
        pending = sum(len(v) for v in streams_by_level.values())
        if pending >= BATCH_SIZE:
            push(loki_url, streams_by_level)
            streams_by_level = {}

    push(loki_url, streams_by_level)
    print(f"Pushed {total} historical log entries to {loki_url}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3100"
    generate(url.rstrip("/"))
