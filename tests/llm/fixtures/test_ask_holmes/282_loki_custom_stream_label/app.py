#!/usr/bin/env python3
"""Log producer for the 282_loki_custom_stream_label eval.

Writes a fixed-size set of JSON log lines to /var/log/checkout.log so the
sidecar Promtail can ingest them into Loki under the `acme_service`
stream label (see deployment.yaml).

The mix is deterministic: 4 ERROR, 2 WARN, 10 INFO. The eval's
expected_output checks the ERROR count.
"""

import json
import os
import time
from datetime import datetime


def emit(level: str, message: str) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "level": level,
        "message": message,
        "acme_service": "checkout",  # stream identity for this cluster
        "pod": os.environ.get("HOSTNAME", "checkout-pod"),
    }
    with open("/var/log/checkout.log", "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


errors = [
    "Payment processor refused: invalid token (txn 9d1f)",
    "Inventory check timed out for SKU-77481",
    "Order persistence failed: deadlock on orders table",
    "Webhook delivery exhausted retries for partner ACME",
]
warns = [
    "Slow database response from orders_db (1.2s)",
    "Rate limiter near threshold for /v1/checkout",
]
infos = [
    f"Checkout flow completed for cart {i}" for i in range(1, 11)
]

for msg in errors:
    emit("ERROR", msg)
for msg in warns:
    emit("WARN", msg)
for msg in infos:
    emit("INFO", msg)

# Keep the pod alive so port-forward / kubectl wait succeed.
while True:
    time.sleep(60)
