#!/usr/bin/env python3
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone

# Lines are written live, each no earlier than its own timestamp, so every
# timestamp falls inside the pod's lifetime. A log that predates the pod reads
# as stale, and careful models dismiss it.
TOTAL_LINES = 8640  # the volume of a day of logs at one line per 10s
DURATION_SECONDS = 30.0
# Expected outputs depend on these: errors from T+15s to T+20s, where T is the
# timestamp of the first log line.
ERROR_WINDOW_SECONDS = (15.0, 20.0)
ERROR_LINES = 36


def entry(timestamp, level, message, **fields):
    return json.dumps(
        {
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": level,
            "service": "scheduler",
            **fields,
            "message": message,
        }
    )


def events():
    """(offset_seconds, line builder) pairs in timestamp order."""
    step = DURATION_SECONDS / TOTAL_LINES
    out = []
    for i in range(TOTAL_LINES):
        offset = (i + 1) * step
        out.append(
            (
                offset,
                lambda ts: entry(
                    ts,
                    "INFO",
                    f"Job executed successfully in {random.randint(100, 500)}ms",
                    job="data-sync",
                    status="success",
                ),
            )
        )
        if i % 100 == 0:
            out.append(
                (
                    offset,
                    lambda ts: entry(
                        ts,
                        "INFO",
                        "System health check passed",
                        job="health-check",
                        status="success",
                    ),
                )
            )
    start, end = ERROR_WINDOW_SECONDS
    for k in range(ERROR_LINES):
        offset = start + (end - start) * k / (ERROR_LINES - 1)
        out.append(
            (
                offset,
                lambda ts: entry(
                    ts,
                    "ERROR",
                    "Job failed - Cannot connect to external API",
                    job="data-sync",
                    status="failed",
                    error="ConnectionError: Failed to establish connection to https://api.external-service.com - Connection timed out",
                ),
            )
        )
    out.sort(key=lambda e: e[0])
    return out


def main():
    first = datetime.now(timezone.utc)
    started = time.monotonic()
    print(entry(first, "INFO", "Scheduler started", job="scheduler"), flush=True)

    for offset, build in events():
        wait = started + offset - time.monotonic()
        if wait > 0:
            sys.stdout.flush()
            time.sleep(wait)
        print(build(first + timedelta(seconds=offset)))

    # A unique last line, so setup knows every line has been written.
    time.sleep(0.5)
    print(
        entry(
            datetime.now(timezone.utc),
            "INFO",
            "Job executed successfully in 167ms.",
            job="data-sync",
            status="success",
        ),
        flush=True,
    )

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
