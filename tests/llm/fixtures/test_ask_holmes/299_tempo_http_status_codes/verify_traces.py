"""Verify Tempo holds /orders traces with exactly the status codes 200, 403 and 409.

Runs inside the checkout pod (piped through `kubectl exec -i ... python -`) so it
reaches Tempo over the cluster network. Exits non-zero until every code is queryable.
"""

import sys
import time

import requests

TEMPO = "http://tempo.app-299.svc.cluster.local:3200"
ORDERS = 'resource.service.name="checkout" && span.http.route="/orders"'
EXPECTED = (200, 403, 409)


def count(condition):
    end = int(time.time())
    resp = requests.get(
        f"{TEMPO}/api/search",
        params={"q": f"{{{ORDERS} && {condition}}}", "limit": 100, "start": end - 3600, "end": end},
        timeout=30,
    )
    resp.raise_for_status()
    return len(resp.json().get("traces", []))


found = {code: count(f"span.http.status_code={code}") for code in EXPECTED}
others = count(" && ".join(f"span.http.status_code!={code}" for code in EXPECTED))
print(f"/orders traces per status code: {found}, other codes: {others}")
if others or any(n < 5 for n in found.values()):
    sys.exit(1)
print("verified: /orders returned exactly 200, 403 and 409")
