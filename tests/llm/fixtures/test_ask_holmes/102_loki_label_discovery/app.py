#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime


def log(message):
    """Write structured log for Promtail."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "level": "WARN" if "warning" in message else "INFO",
        "message": message,
        "service": "inventory-service",
        "pod": os.environ.get("HOSTNAME", "inventory-service-pod"),
    }
    with open("/var/log/inventory.log", "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


# Generate the required logs
logs = [
    "Inventory item ITEM-1234 checked - stock level: 45 units",
    "Order ORD-5678 processed - 3 units of ITEM-1234",
    "Stock updated for ITEM-1234 - new level: 42 units",
    "Low stock warning for ITEM-9999 - current level: 5 units",
    "Inventory reconciliation completed - 127 items verified",
]

for msg in logs:
    log(msg)
    time.sleep(0.5)

# Keep running with periodic logs
while True:
    time.sleep(10)
    log("Inventory check completed - all systems operational")
