"""Verify Tempo holds /checkout traces where every slow request carries the promo coupon.

Runs inside the checkout pod (piped through `kubectl exec -i ... python -`) so it
reaches Tempo over the cluster network. Exits non-zero until the pattern is queryable.
"""

import sys
import time

import requests

TEMPO = "http://tempo.app-298.svc.cluster.local:3200"
PROMO = '{resource.service.name="checkout" && span.http.target=~".*coupon=Promotions.*"}'
PLAIN = '{resource.service.name="checkout" && name="POST /checkout" && span.http.target!~".*coupon=Promotions.*"}'
MIN_TRACES = 20
SLOW_MS = 300


def search(query):
    end = int(time.time())
    resp = requests.get(
        f"{TEMPO}/api/search",
        params={"q": query, "limit": 500, "start": end - 3600, "end": end},
        timeout=30,
    )
    resp.raise_for_status()
    return [t.get("durationMs", 0) for t in resp.json().get("traces", [])]


promo = search(PROMO)
plain = search(PLAIN)
print(f"promo traces={len(promo)} plain traces={len(plain)}")
if len(promo) < MIN_TRACES or len(plain) < MIN_TRACES:
    sys.exit(1)

promo_avg = sum(promo) / len(promo)
plain_avg = sum(plain) / len(plain)
slow_plain = [d for d in plain if d >= SLOW_MS]
print(f"promo avg={promo_avg:.0f}ms plain avg={plain_avg:.0f}ms slow plain={len(slow_plain)}")
if promo_avg < 3 * plain_avg or slow_plain:
    sys.exit(1)
print("verified: only promo-code requests are slow")
