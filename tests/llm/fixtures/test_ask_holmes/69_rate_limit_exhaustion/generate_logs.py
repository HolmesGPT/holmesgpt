#!/usr/bin/env python3
import json
from datetime import datetime
import time
import random


def generate_log_entry(status=200, req_per_sec=10, client_ip=None, message="OK"):
    timestamp = datetime.utcnow()

    return json.dumps(
        {
            "timestamp": timestamp.isoformat() + "Z",
            "level": "ERROR" if status == 429 else "INFO",
            "service": "api-limiter",
            "method": "POST",
            "path": "/api/data/ingest",
            "status": status,
            "message": message,
            "client_ip": client_ip
            or f"10.0.{random.randint(1,254)}.{random.randint(1,254)}",
            "requests_per_second": req_per_sec,
            "rate_limit": "100/second",
            "request_id": f"req-{random.randint(100000, 999999)}",
        }
    )


def main():
    # Generate 80,000 normal traffic logs (10 req/sec)
    for i in range(80000):
        print(
            generate_log_entry(
                status=200,
                req_per_sec=random.randint(8, 12),
                message="Request processed successfully",
            )
        )

    # Sudden spike from a specific client (position: 80% through logs)
    spike_client = "10.0.50.123"

    # Log showing spike detection
    print(
        json.dumps(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "WARN",
                "service": "api-limiter",
                "message": f"Traffic spike detected from {spike_client}: 1000 requests/second",
                "client_ip": spike_client,
                "requests_per_second": 1000,
                "rate_limit_threshold": 100,
            }
        )
    )

    # Generate 60 seconds of spike traffic (1000 req/sec)
    for i in range(60000):
        print(
            generate_log_entry(
                status=200,
                req_per_sec=1000,
                client_ip=spike_client,
                message="Request processed (high rate)",
            )
        )

    # Rate limiting kicks in - 5,000 rejected requests
    for i in range(5000):
        print(
            generate_log_entry(
                status=429,
                req_per_sec=1000,
                client_ip=spike_client,
                message="Too Many Requests - Rate limit exceeded (100/second)",
            )
        )

    # Back to normal traffic
    for i in range(5000):
        print(
            generate_log_entry(
                status=200,
                req_per_sec=random.randint(8, 12),
                message="Request processed successfully",
            )
        )

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
