#!/usr/bin/env python3
import json
import random
from datetime import datetime, timedelta
import time


def generate_log_entry(status=200, message="OK", path="/api/health", level="INFO"):
    timestamp = datetime.utcnow() - timedelta(seconds=random.randint(0, 3600))
    return json.dumps(
        {
            "timestamp": timestamp.isoformat() + "Z",
            "level": "ERROR" if status >= 500 else level,
            "service": "web-server",
            "method": "GET",
            "path": path,
            "status": status,
            "response_time_ms": random.randint(50, 200),
            "message": message,
            "client_ip": f"10.0.{random.randint(1,254)}.{random.randint(1,254)}",
            "request_id": f"req-{random.randint(100000, 999999)}",
        }
    )


def main():
    # Generate 50,000 successful requests
    for i in range(50000):
        print(generate_log_entry())

    # The critical error in the middle
    print(
        generate_log_entry(
            status=500,
            message="Database connection timeout: Could not acquire connection from pool after 30s",
            path="/api/orders",
        )
    )

    # Another 50,000 successful requests
    for i in range(50000):
        print(generate_log_entry())

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
