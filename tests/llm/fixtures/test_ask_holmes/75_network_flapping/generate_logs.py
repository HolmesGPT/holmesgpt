#!/usr/bin/env python3
import json
from datetime import datetime
import time
import random


def generate_log_entry(
    status="success", response_time=None, error=None, timeout_rate=0
):
    timestamp = datetime.utcnow()

    entry = {
        "timestamp": timestamp.isoformat() + "Z",
        "level": "ERROR" if error else "WARN" if timeout_rate > 0.1 else "INFO",
        "service": "frontend",
        "endpoint": "/api/backend",
        "status": status,
    }

    if response_time:
        entry["response_time_ms"] = response_time
    if error:
        entry["error"] = error
        entry["message"] = error
    else:
        entry["message"] = f"Request completed - Status: {status}"
    if timeout_rate > 0:
        entry["network_timeout_rate"] = round(timeout_rate, 3)

    return json.dumps(entry)


def main():
    # Pattern: Network issues that gradually worsen
    # Phase 1: 1 timeout per 1000 requests (0.1%)
    # Phase 2: 1 timeout per 100 requests (1%)
    # Phase 3: 1 timeout per 10 requests (10%)
    # Phase 4: More timeouts than successes (>50%)

    total_requests = 0
    total_timeouts = 0

    # Phase 1: Very occasional timeouts (25,000 requests)
    print(
        json.dumps(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "INFO",
                "service": "frontend",
                "message": "Network monitoring started",
                "phase": "normal_operation",
            }
        )
    )

    for i in range(25000):
        total_requests += 1
        if i % 1000 == 500:  # 1 in 1000
            total_timeouts += 1
            print(
                generate_log_entry(
                    status="timeout",
                    error="Network timeout: Connection to backend timed out after 30000ms",
                    timeout_rate=total_timeouts / total_requests,
                )
            )
        else:
            print(
                generate_log_entry(
                    status="success", response_time=random.randint(50, 200)
                )
            )

    # Phase 2: Degrading (25,000 requests)
    print(
        json.dumps(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "WARN",
                "service": "frontend",
                "message": "Network degradation detected - timeout rate increasing",
                "timeout_rate": round(total_timeouts / total_requests, 3),
            }
        )
    )

    for i in range(25000):
        total_requests += 1
        if i % 100 == 50:  # 1 in 100
            total_timeouts += 1
            print(
                generate_log_entry(
                    status="timeout",
                    error="Network timeout: Connection to backend timed out after 30000ms",
                    timeout_rate=total_timeouts / total_requests,
                )
            )
        else:
            print(
                generate_log_entry(
                    status="success",
                    response_time=random.randint(100, 500),  # Slower responses
                )
            )

    # Phase 3: Significant issues (25,000 requests)
    print(
        json.dumps(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "ERROR",
                "service": "frontend",
                "message": "Network issues critical - high timeout rate detected",
                "timeout_rate": round(total_timeouts / total_requests, 3),
                "alert": "Network flapping detected between frontend and backend",
            }
        )
    )

    for i in range(25000):
        total_requests += 1
        if i % 10 == 5:  # 1 in 10
            total_timeouts += 1
            print(
                generate_log_entry(
                    status="timeout",
                    error="Network timeout: Connection to backend timed out after 30000ms",
                    timeout_rate=total_timeouts / total_requests,
                )
            )
        else:
            print(
                generate_log_entry(
                    status="success",
                    response_time=random.randint(500, 2000),  # Much slower
                )
            )

    # Phase 4: Critical failure (25,000 requests)
    print(
        json.dumps(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "ERROR",
                "service": "frontend",
                "message": "CRITICAL: Network connectivity severely degraded",
                "timeout_rate": round(total_timeouts / total_requests, 3),
                "recommendation": "Investigate network path between frontend and backend services",
            }
        )
    )

    for i in range(25000):
        total_requests += 1
        if random.random() < 0.6:  # 60% failure rate
            total_timeouts += 1
            print(
                generate_log_entry(
                    status="timeout",
                    error="Network timeout: Connection to backend timed out after 30000ms",
                    timeout_rate=total_timeouts / total_requests,
                )
            )
        else:
            print(
                generate_log_entry(
                    status="success",
                    response_time=random.randint(
                        2000, 5000
                    ),  # Very slow when successful
                )
            )

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
