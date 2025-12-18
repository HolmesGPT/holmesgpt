#!/usr/bin/env python3
import json
from datetime import datetime, timedelta
import time
import random


def generate_log_entry(service, level, message, timestamp_offset=0, trace_id=None):
    timestamp = datetime.utcnow() - timedelta(seconds=timestamp_offset)
    entry = {
        "timestamp": timestamp.isoformat() + "Z",
        "level": level,
        "service": service,
        "message": message,
        "instance_id": f"{service}-{random.randint(1,3)}",
    }
    if trace_id:
        entry["trace_id"] = trace_id
    return json.dumps(entry)


def main():
    # Generate normal logs for first 10,000 entries
    services = ["auth-service", "user-service", "order-service", "payment-processor"]

    for i in range(10000):
        service = random.choice(services)
        print(
            generate_log_entry(
                service,
                "INFO",
                "Processing request successfully",
                timestamp_offset=3600 - i * 0.3,
            )
        )

    # The cascading failure starts here (around 10% into logs)
    base_time = 3600 - 10000 * 0.3

    # Root cause: Redis connection lost in auth-service
    print(
        generate_log_entry(
            "auth-service",
            "ERROR",
            "Lost connection to Redis: Connection refused (ECONNREFUSED) redis-master:6379",
            timestamp_offset=base_time,
        )
    )

    # 5 seconds later: user-service fails
    print(
        generate_log_entry(
            "user-service",
            "ERROR",
            "Auth service unavailable: Failed to validate token - auth-service returned 503",
            timestamp_offset=base_time - 5,
            trace_id="trace-failure-001",
        )
    )

    # 10 seconds later: order-service fails
    print(
        generate_log_entry(
            "order-service",
            "ERROR",
            "Cannot validate user: user-service responded with 503 Service Unavailable",
            timestamp_offset=base_time - 10,
            trace_id="trace-failure-001",
        )
    )

    # 15 seconds later: payment-processor fails
    print(
        generate_log_entry(
            "payment-processor",
            "ERROR",
            "Order validation failed: Unable to process payment - order-service returned error",
            timestamp_offset=base_time - 15,
            trace_id="trace-failure-001",
        )
    )

    # More error logs showing the cascade
    for i in range(100):
        service = random.choice(services)
        if service == "auth-service":
            msg = "Redis connection failed: Connection pool exhausted"
        elif service == "user-service":
            msg = "Authentication service dependency failure"
        elif service == "order-service":
            msg = "User validation service unavailable"
        else:
            msg = "Upstream service chain failure"

        print(
            generate_log_entry(
                service, "ERROR", msg, timestamp_offset=base_time - 20 - i * 0.5
            )
        )

    # Generate more normal logs to bury the errors
    for i in range(89900):
        service = random.choice(services)
        print(
            generate_log_entry(
                service,
                "INFO",
                "Processing request successfully",
                timestamp_offset=base_time - 100 - i * 0.3,
            )
        )

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
