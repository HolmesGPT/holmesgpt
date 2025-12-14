#!/usr/bin/env python3
import os
import json
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import random

random.seed(100)


def log_structured(level, message, timestamp=None, **kwargs):
    """Log in structured format for Loki."""
    if timestamp is None:
        timestamp = datetime.utcnow()

    log_entry = {
        "timestamp": timestamp.isoformat() + "Z",
        "level": level,
        "message": message,
        "service": "payment-api",
        "pod": os.environ.get("HOSTNAME", "payment-api-pod"),
        **kwargs,
    }
    with open("/var/log/payment-api.log", "a") as f:
        f.write(json.dumps(log_entry) + "\n")
        f.flush()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging


def generate_historical_logs():
    """Generate minimal historical logs with the incident."""
    problem_start = datetime(2025, 8, 2, 13, 45, 0)
    problem_end = datetime(2025, 8, 2, 14, 45, 0)

    # Start from August 1 to have some context
    current_time = datetime(2025, 8, 1, 12, 0, 0)
    scenario_end = datetime(2025, 8, 4, 14, 0, 0)

    while current_time < scenario_end:
        # Minimal normal logs
        if random.random() < 0.05:
            log_structured(
                "INFO",
                "Payment processed successfully",
                timestamp=current_time,
                payment_id=f"PAY-{random.randint(1000, 9999)}",
            )

        # During incident period, generate the required errors
        if problem_start <= current_time <= problem_end:
            # The key error message Holmes needs to find
            if random.random() < 0.4:
                log_structured(
                    "ERROR",
                    "Failed to acquire database connection - pool exhausted",
                    timestamp=current_time,
                    wait_time_ms=random.randint(1000, 5000),
                    queue_length=random.randint(5, 15),
                )

        # Advance time
        current_time += timedelta(minutes=random.randint(1, 5))


def main():
    # Generate all historical logs immediately
    generate_historical_logs()

    # Start minimal HTTP server
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
    log_structured("INFO", "Payment API started", port=8080)

    # Keep server running (pod will be deleted before investigation)
    server.serve_forever()


if __name__ == "__main__":
    main()
