"""Verify Tempo holds complete payments_cycle traces whose slow part is in order-management.

Runs inside the payment-processor pod (piped through `kubectl exec -i ... python -`)
so it reaches Tempo over the cluster network. Exits non-zero until enough traces exist.
"""

import sys
import time

import requests

TEMPO = "http://tempo.app-300.svc.cluster.local:3200"
MIN_TRACES = 3
QUERIES = {
    "slow order-management": '{name="payments_cycle"} >> {resource.service.name="order-management" && duration > 1s}',
    "reached transaction-monitoring": '{name="payments_cycle"} >> {resource.service.name="transaction-monitoring"}',
    "errors": "{status=error}",
}


def count(query):
    end = int(time.time())
    resp = requests.get(
        f"{TEMPO}/api/search",
        params={"q": query, "limit": 50, "start": end - 3600, "end": end},
        timeout=30,
    )
    resp.raise_for_status()
    return len(resp.json().get("traces", []))


counts = {label: count(q) for label, q in QUERIES.items()}
print(f"payments_cycle traces: {counts}")
if counts["errors"]:
    print("unexpected error spans in Tempo")
    sys.exit(2)
if counts["slow order-management"] < MIN_TRACES or counts["reached transaction-monitoring"] < MIN_TRACES:
    sys.exit(1)
print("verified: payments_cycle traces show order-management latency")
