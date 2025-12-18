#!/usr/bin/env python3
import time
import random
import sys
from datetime import datetime


def generate_web_logs():
    """Generate web container logs"""
    for i in range(20):
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        print(
            f"[{timestamp}] [web container] Starting API server on port 8080",
            flush=True,
        )
        print(f"[{timestamp}] [web container] Processing API requests...", flush=True)
        print(
            f"[{timestamp}] [web container] GET /api/users - 200 OK - {random.randint(10, 50)}ms",
            flush=True,
        )
        print(
            f"[{timestamp}] [web container] POST /api/orders - 201 Created - {random.randint(50, 150)}ms",
            flush=True,
        )
        time.sleep(2)

    # Keep running
    while True:
        time.sleep(60)


def generate_sidecar_logs():
    """Generate sidecar container logs"""
    for i in range(20):
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        print(f"[{timestamp}] [sidecar container] Collecting metrics...", flush=True)
        print(
            f"[{timestamp}] [sidecar container] CPU usage: {random.randint(10, 30)}%",
            flush=True,
        )
        print(
            f"[{timestamp}] [sidecar container] Memory usage: {random.randint(100, 200)}MB",
            flush=True,
        )
        print(
            f"[{timestamp}] [sidecar container] Active connections: {random.randint(5, 50)}",
            flush=True,
        )
        time.sleep(3)

    # Keep running
    while True:
        time.sleep(60)


if __name__ == "__main__":
    container = sys.argv[1] if len(sys.argv) > 1 else "web"

    if container == "web":
        generate_web_logs()
    elif container == "sidecar":
        generate_sidecar_logs()
