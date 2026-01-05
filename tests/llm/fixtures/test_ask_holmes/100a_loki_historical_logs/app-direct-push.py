#!/usr/bin/env python3
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# Set random seed for reproducible logs
random.seed(100)

LOKI_URL = "http://loki:3100/loki/api/v1/push"
BATCH_SIZE = 100
BATCH_INTERVAL = 1  # seconds


class LogBatcher:
    """Batches logs and sends them to Loki"""

    def __init__(self):
        self.batch = []
        self.lock = threading.Lock()
        self.last_flush = time.time()
        threading.Thread(target=self._flush_periodically, daemon=True).start()

    def add_log(self, timestamp, level, message, **kwargs):
        """Add a log entry to the batch"""
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Convert timestamp to nanoseconds since epoch
        if isinstance(timestamp, datetime):
            ts_nano = str(int(timestamp.timestamp() * 1e9))
        else:
            ts_nano = str(timestamp)

        # Build log line
        log_data = {
            "level": level,
            "message": message,
            "service": "payment-api",
            "pod": os.environ.get("HOSTNAME", "payment-api-pod"),
            **kwargs,
        }
        log_line = json.dumps(log_data)

        with self.lock:
            self.batch.append([ts_nano, log_line])
            if len(self.batch) >= BATCH_SIZE:
                self._flush()

    def _flush(self):
        """Send batch to Loki"""
        if not self.batch:
            return

        # Group logs by level
        streams_by_level = {}
        for ts, log_line in self.batch[:BATCH_SIZE]:
            try:
                log_data = json.loads(log_line)
                level = log_data.get("level", "INFO")
            except json.JSONDecodeError:
                level = "INFO"

            if level not in streams_by_level:
                streams_by_level[level] = []
            streams_by_level[level].append([ts, log_line])

        # Prepare Loki push payload with separate streams per level
        pod_name = os.environ.get("HOSTNAME", "payment-api-pod")
        streams = []
        for level, values in streams_by_level.items():
            streams.append(
                {
                    "stream": {
                        "job": "payment-api",
                        "namespace": "app-100a",
                        "pod_name": pod_name,  # Standard label expected by Holmes
                        "level": level,
                        "service": "payment-api",
                    },
                    "values": values,
                }
            )

        payload = {"streams": streams}

        # Clear the batch we're sending
        self.batch = self.batch[BATCH_SIZE:]

        # Send to Loki
        try:
            req = urllib.request.Request(
                LOKI_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = urllib.request.urlopen(req, timeout=10)
            if response.status == 204:
                total_logs = sum(len(s["values"]) for s in payload["streams"])
                print(
                    f"✓ Pushed {total_logs} logs to Loki ({len(payload['streams'])} streams)"
                )
        except Exception as e:
            print(f"✗ Failed to push logs to Loki: {e}")

    def _flush_periodically(self):
        """Flush logs periodically"""
        while True:
            time.sleep(BATCH_INTERVAL)
            with self.lock:
                if self.batch and time.time() - self.last_flush > BATCH_INTERVAL:
                    self._flush()
                    self.last_flush = time.time()

    def flush_all(self):
        """Force flush all remaining logs"""
        with self.lock:
            while self.batch:
                self._flush()


# Global log batcher
log_batcher = LogBatcher()


def log_structured(level, message, timestamp=None, **kwargs):
    """Log in structured format and send to Loki"""
    log_batcher.add_log(timestamp, level, message, **kwargs)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"status": "healthy", "uptime_seconds": 60}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Log health checks occasionally
        if random.random() < 0.1:
            log_structured(
                "INFO",
                "Health check passed",
                endpoint="/healthz",
                response_time_ms=random.randint(50, 200),
            )


def generate_historical_logs():
    """Generate all historical logs including the incident period."""
    print("Starting historical log generation...")

    # Define the problematic period
    problem_start = datetime(2025, 8, 2, 13, 45, 0)
    problem_end = datetime(2025, 8, 2, 14, 45, 0)

    # Generate logs from August 1 to August 4
    current_time = datetime(2025, 8, 1, 0, 0, 0)
    scenario_now = datetime(2025, 8, 4, 14, 0, 0)

    log_count = 0

    while current_time < scenario_now:
        # Normal operation logs
        if random.random() < 0.1:
            log_structured(
                "INFO",
                "Payment processed successfully",
                timestamp=current_time,
                payment_id=f"PAY-{random.randint(1000, 9999)}",
                amount=round(random.uniform(10, 1000), 2),
                currency="USD",
                processing_time_ms=random.randint(100, 500),
            )
            log_count += 1

        if random.random() < 0.05:
            log_structured(
                "INFO",
                "User authenticated",
                timestamp=current_time,
                user_id=f"USER-{random.randint(100, 999)}",
                method="oauth2",
                ip_address=f"192.168.{random.randint(1,254)}.{random.randint(1,254)}",
            )
            log_count += 1

        # Database query logs
        if random.random() < 0.2:
            query_time = random.randint(10, 100)
            if problem_start <= current_time <= problem_end:
                query_time = random.randint(1000, 5000)  # Slow queries during issues

            log_structured(
                "DEBUG",
                "Database query executed",
                timestamp=current_time,
                query="SELECT * FROM payments WHERE status = ?",
                duration_ms=query_time,
                rows_returned=random.randint(0, 100),
            )
            log_count += 1

        # Cache operations
        if random.random() < 0.15:
            hit_rate = 0.85 if current_time < problem_start else 0.3
            log_structured(
                "DEBUG",
                "Cache operation",
                timestamp=current_time,
                operation=random.choice(["get", "set", "expire"]),
                key=f"user:{random.randint(100, 999)}:profile",
                hit=random.random() < hit_rate,
            )
            log_count += 1

        # ERROR logs during the incident period
        if problem_start <= current_time <= problem_end:
            if random.random() < 0.3:
                log_structured(
                    "ERROR",
                    "Failed to acquire database connection - pool exhausted",
                    timestamp=current_time,
                    pool_size=20,
                    active_connections=20,
                    waiting_requests=random.randint(5, 50),
                    wait_time_ms=random.randint(5000, 30000),
                )
                log_count += 1

            if random.random() < 0.2:
                log_structured(
                    "ERROR",
                    "ConnectionPoolExhausted: All connections in use",
                    timestamp=current_time,
                    error_code="DB_CONN_001",
                    stack_trace="at PaymentService.processPayment()\\n  at DatabasePool.acquire()",
                )
                log_count += 1

            if random.random() < 0.1:
                log_structured(
                    "ERROR",
                    "Health check failed - database unreachable",
                    timestamp=current_time,
                    endpoint="/healthz",
                    status_code=503,
                    response_time_ms=random.randint(5000, 10000),
                )
                log_count += 1

        # Increment time
        current_time += timedelta(seconds=random.randint(20, 180))

        # Flush periodically
        if log_count % 50 == 0:
            log_batcher.flush_all()
            print(f"Generated {log_count} historical logs so far...")

    # Final flush
    log_batcher.flush_all()
    print(f"Historical log generation complete! Generated {log_count} logs total")


def main():
    # Generate all historical logs first
    generate_historical_logs()

    # Wait a bit to ensure all logs are pushed
    time.sleep(5)

    # Start health endpoint server
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
    print("Starting HTTP server on port 8080...")

    # Add startup logs
    log_structured("INFO", "Payment API started", port=8080, version="1.2.3")
    log_structured(
        "INFO",
        "Initializing database connection pool",
        pool_size=20,
        min_connections=5,
        max_connections=50,
    )
    log_structured(
        "INFO",
        "Database connection established",
        host="postgres-primary",
        port=5432,
        database="payments",
    )

    # Generate real-time logs
    def real_time_logs():
        while True:
            log_type = random.random()
            if log_type < 0.6:
                log_structured(
                    "INFO",
                    "Payment processed successfully",
                    payment_id=f"PAY-{random.randint(1000, 9999)}",
                    amount=round(random.uniform(10, 1000), 2),
                    currency="USD",
                    processing_time_ms=random.randint(100, 500),
                )
            elif log_type < 0.75:
                log_structured(
                    "DEBUG",
                    "Database query executed",
                    query="SELECT * FROM payments WHERE user_id = ?",
                    duration_ms=random.randint(5, 50),
                    rows_returned=random.randint(0, 10),
                )
            elif log_type < 0.85:
                log_structured(
                    "INFO",
                    "User authenticated",
                    user_id=f"USER-{random.randint(100, 999)}",
                    method=random.choice(["oauth2", "api_key", "jwt"]),
                )
            elif log_type < 0.95:
                log_structured(
                    "DEBUG",
                    "Cache operation",
                    operation=random.choice(["get", "set"]),
                    key=f"session:{random.randint(1000, 9999)}",
                    hit=random.choice([True, False]),
                )
            time.sleep(random.randint(3, 8))

    rt_thread = threading.Thread(target=real_time_logs, daemon=True)
    rt_thread.start()

    server.serve_forever()


if __name__ == "__main__":
    main()
