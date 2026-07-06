#!/usr/bin/env python3
import json
import random
import sys
import time
from datetime import datetime, timedelta

# The full log history is emitted immediately at container start so the eval's
# setup can verify it deterministically. The incident timeline is carried by
# backdated timestamps spread over the window below, ending at "now".
HISTORY_WINDOW = timedelta(hours=1)
TOTAL_RECORDS = 2000

_start = None
_step = None


def _ts(sequence_num):
    return (_start + _step * sequence_num).isoformat() + "Z"


def generate_log_entry(
    active_connections,
    max_connections=100,
    wait_time=None,
    error=None,
    sequence_num=None,
):
    level = "INFO"
    message = f"Database pool status: {active_connections}/{max_connections} connections active"

    if error:
        level = "ERROR"
        message = error
    elif active_connections >= 95:
        level = "WARN"
        message = (
            f"Connection pool nearly exhausted: {active_connections}/{max_connections}"
        )
    elif active_connections >= 80:
        level = "WARN"
        message = f"High connection pool usage: {active_connections}/{max_connections}"

    entry = {
        "timestamp": _ts(sequence_num or 0),
        "level": level,
        "service": "backend-service",
        "component": "database-pool",
        "active_connections": active_connections,
        "max_connections": max_connections,
        "pool_usage_percent": round((active_connections / max_connections) * 100, 2),
        "message": message,
    }

    if wait_time:
        entry["connection_wait_ms"] = wait_time

    if sequence_num is not None:
        entry["seq"] = sequence_num

    return json.dumps(entry)


def main():
    global _start, _step
    _start = datetime.utcnow() - HISTORY_WINDOW
    _step = HISTORY_WINDOW / TOTAL_RECORDS

    lines = []
    seq = 0

    # Normal operation for first ~1000 logs (5-20 connections)
    # Was 50,000, now 1000 (50% of total)
    for i in range(1000):
        connections = random.randint(5, 20)
        lines.append(generate_log_entry(connections, sequence_num=seq))
        seq += 1

    # Gradual increase during peak hours
    # Was 10,000, now 200 (10% of total)
    for i in range(200):
        # Gradually increase from 20 to 80 connections
        connections = 20 + int((i / 200) * 60)
        wait_time = None if connections < 70 else random.randint(100, 500)
        lines.append(generate_log_entry(connections, wait_time=wait_time, sequence_num=seq))
        seq += 1

    # Warning phase - approaching limit
    lines.append(
        json.dumps(
            {
                "timestamp": _ts(seq),
                "level": "WARN",
                "service": "backend-service",
                "component": "database-pool",
                "message": "Connection wait time increasing: Average wait 500ms",
                "avg_wait_ms": 500,
                "active_connections": 80,
                "seq": seq,
            }
        )
    )
    seq += 1

    # Continue increasing to near limit
    # Was 5,000, now 100 (5% of total)
    for i in range(100):
        connections = 80 + int((i / 100) * 15)  # 80 to 95
        wait_time = random.randint(500, 2000)
        lines.append(generate_log_entry(connections, wait_time=wait_time, sequence_num=seq))
        seq += 1

    # Critical phase - pool exhausted
    lines.append(
        json.dumps(
            {
                "timestamp": _ts(seq),
                "level": "ERROR",
                "service": "backend-service",
                "component": "database-pool",
                "message": "Connection pool exhausted!",
                "active_connections": 100,
                "max_connections": 100,
                "rejected_requests": 0,
                "seq": seq,
            }
        )
    )
    seq += 1

    # Multiple timeout errors
    # Was 1,000, now 20 (1% of total)
    for i in range(20):
        lines.append(
            generate_log_entry(
                100,
                error="Timeout waiting for database connection after 30000ms - Pool exhausted (100/100)",
                sequence_num=seq,
            )
        )
        seq += 1

        # Some successful requests that managed to get connections
        if i % 10 == 0:
            lines.append(
                generate_log_entry(
                    100, wait_time=random.randint(25000, 30000), sequence_num=seq
                )
            )
            seq += 1

    # More normal logs after the incident
    # Was 34,000, now ~677 to reach 2000 total
    remaining = 2000 - seq
    for i in range(remaining):
        connections = random.randint(5, 20)
        lines.append(generate_log_entry(connections, sequence_num=seq))
        seq += 1

    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
