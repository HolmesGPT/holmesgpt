#!/usr/bin/env python3
import json
from datetime import datetime
import time
import math


def generate_log_entry(response_time_ms, status="success", message=None):
    timestamp = datetime.utcnow()

    if response_time_ms > 5000:
        status = "timeout"
        message = "Request timeout after 5000ms"
    elif response_time_ms > 3000:
        status = "slow"
        message = "Response time exceeding SLA"

    return json.dumps(
        {
            "timestamp": timestamp.isoformat() + "Z",
            "level": "ERROR"
            if status == "timeout"
            else "WARN"
            if status == "slow"
            else "INFO",
            "service": "api-gateway",
            "endpoint": "/api/process",
            "response_time_ms": response_time_ms,
            "status": status,
            "message": message or f"Request processed in {response_time_ms}ms",
            "cpu_usage": min(
                95, 20 + (response_time_ms / 100)
            ),  # CPU increases with response time
            "memory_mb": 256 + (response_time_ms / 50),  # Memory also increases
        }
    )


def main():
    # Generate 100,000 logs showing gradual degradation
    base_time = 100  # Start at 100ms

    for i in range(100000):
        # Exponential growth in response time
        # At i=0: 100ms, at i=50000: ~500ms, at i=90000: ~3000ms, at i=100000: timeout
        response_time = int(base_time * math.exp(i / 20000))

        # Cap at 6000ms to show timeouts
        response_time = min(6000, response_time)

        print(generate_log_entry(response_time))

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
