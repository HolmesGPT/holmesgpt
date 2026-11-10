"""Write the logs of coral-reef-7b9f4fd5c9-xk2lm, a pod that ran ~9 hours ago, to Loki."""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://loki:3100/loki/api/v1/push")
NAMESPACE = os.getenv("TARGET_NAMESPACE", "app-302")
POD = "coral-reef-7b9f4fd5c9-xk2lm"

# Offsets in milliseconds from the moment the pod went critical, ~9 hours ago.
MIN = 60_000
ENTRIES = [
    (-7 * MIN, "INFO", "Starting api-gateway service v2.3.1"),
    (-7 * MIN + 1000, "INFO", "Initializing database connection pool with size=50"),
    (-7 * MIN + 2000, "INFO", "Connected to database successfully"),
    (-7 * MIN + 3000, "INFO", "HTTP server listening on port 8080"),
    (-7 * MIN + 10000, "INFO", "Health check endpoint ready"),
    (-7 * MIN + 15000, "INFO", "Serving traffic normally, current memory: 412MB"),
    (-180_000, "INFO", "Started processing large batch import job ID: batch-2943"),
    (-170_000, "DEBUG", "Batch import: loading 50000 records into memory"),
    (-160_000, "DEBUG", "RequestHandler: buffer pool size increased to 256MB"),
    (-150_000, "WARN", "GC overhead limit: 98% time spent in garbage collection"),
    (-120_000, "WARN", "Memory usage increasing: 680MB / 1024MB"),
    (-90_000, "INFO", "Processing batch job: 1000 records"),
    (-60_000, "WARN", "Database connection pool usage: 45/50 connections"),
    (-30_000, "WARN", "Memory usage high: 850MB / 1024MB"),
    (
        0,
        "ERROR",
        "Database connection pool exhausted - MaxConnectionsReached: All 50 connections in use",
    ),
    (1000, "ERROR", "Failed to acquire database connection - timeout after 30s"),
    (2000, "WARN", "Memory usage critical: 980MB / 1024MB"),
    (
        3000,
        "ERROR",
        "Request handler failed: java.lang.OutOfMemoryError: Java heap space",
    ),
    (4000, "ERROR", "Cannot allocate memory for new request buffer"),
    (5000, "ERROR", "Memory allocation failed in request processor"),
    (
        10000,
        "ERROR",
        "RequestHandler memory leak detected: 512MB unreleased buffers",
    ),
    (11000, "ERROR", "Failed to process request: java.lang.OutOfMemoryError"),
    (12000, "CRITICAL", "JVM heap dump triggered due to OutOfMemoryError"),
    (60000, "ERROR", "500 errors returned to clients - service unavailable"),
    (70000, "ERROR", "Health check failed - service unresponsive"),
    (80000, "ERROR", "Circuit breaker opened due to repeated failures"),
    (120000, "ERROR", "Application terminating: OutOfMemoryError"),
    (
        121000,
        "FATAL",
        "Process killed - OOM killer triggered (memory usage: 1024MB/1024MB)",
    ),
]


def main():
    critical = datetime.now(timezone.utc) - timedelta(hours=9)
    streams = {}
    for offset_ms, level, message in ENTRIES:
        ts = critical + timedelta(milliseconds=offset_ms)
        line = f"{ts.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z {level:<5} {message}"
        streams.setdefault(level.lower(), []).append(
            [str(int(ts.timestamp() * 1000) * 1_000_000), line]
        )

    payload = {
        "streams": [
            {
                "stream": {
                    "namespace": NAMESPACE,
                    "pod": POD,
                    "container": "coral-reef",
                    "app": "coral-reef",
                    "job": f"{NAMESPACE}/coral-reef",
                    "node_name": "node-03.k8s.cluster",
                    "level": level,
                },
                "values": sorted(values),
            }
            for level, values in streams.items()
        ]
    }
    req = urllib.request.Request(
        LOKI_PUSH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"Loki accepted {len(ENTRIES)} lines (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"Loki rejected the push: HTTP {e.code}: {e.read().decode()}")
        sys.exit(1)
    print(f"critical_time={critical.isoformat()}")


if __name__ == "__main__":
    main()
