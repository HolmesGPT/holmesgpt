"""Generate API gateway log data for deploy regression test.

Creates a realistic dataset showing a post-deploy regression where organization-linked
accounts get 403 errors on the billing endpoint, while solo accounts are unaffected.

Data layout:
  Pre-deploy (v2.13.2, 08:00-09:40): All requests succeed (200)
    - 15 solo user requests across 3 endpoints
    - 15 org user requests across 3 endpoints

  Deploy event (09:45): Version v2.14.0-rc3 rollout marker

  Post-deploy (v2.14.0-rc3, 09:50-10:40): Regression appears
    - 15 solo user requests across 3 endpoints → 200
    - 5 solo user requests on billing endpoint → 200 (billing works for solo)
    - 10 org user requests on non-billing endpoints → 200
    - 15 org user requests on billing endpoint → 403 (AUTHZ-4821)

Total: 76 documents
"""

import json
import random

random.seed(217)

INDEX_ACTION = json.dumps({"index": {}})

SOLO_USERS = ["usr-4821", "usr-7293", "usr-1054", "usr-8367", "usr-5940"]
ORG_USERS = [
    ("usr-2186", "org-8f4k2m7p"),
    ("usr-6734", "org-8f4k2m7p"),
    ("usr-9012", "org-3nq9v2xd"),
    ("usr-3457", "org-3nq9v2xd"),
    ("usr-7801", "org-kw5j7r1t"),
]
ENDPOINTS = ["/api/v3/billing/invoices", "/api/v3/users/profile", "/api/v3/projects/list"]
NON_BILLING = ["/api/v3/users/profile", "/api/v3/projects/list"]

minute_counter = 0


def next_timestamp():
    global minute_counter
    h = 8 + minute_counter // 60
    m = minute_counter % 60
    minute_counter += 2
    return f"2025-03-15T{h:02d}:{m:02d}:00Z"


def emit(doc):
    print(INDEX_ACTION)
    print(json.dumps(doc))


def success_request(ts, method, endpoint, user_id, version, org_id=None):
    doc = {
        "@timestamp": ts,
        "method": method,
        "endpoint": endpoint,
        "status_code": 200,
        "user_id": user_id,
        "deploy_version": version,
        "response_time_ms": random.randint(25, 120),
        "request_id": f"req-{random.randint(100000, 999999)}",
    }
    if org_id:
        doc["org_id"] = org_id
    emit(doc)


def forbidden_request(ts, method, endpoint, user_id, version, org_id):
    emit({
        "@timestamp": ts,
        "method": method,
        "endpoint": endpoint,
        "status_code": 403,
        "user_id": user_id,
        "org_id": org_id,
        "deploy_version": version,
        "response_time_ms": random.randint(3, 12),
        "error_code": "AUTHZ-4821",
        "error_message": f"Authorization failed: required scope 'org:billing:read' not present in token for organization {org_id}",
        "request_id": f"req-{random.randint(100000, 999999)}",
    })


# === Pre-deploy traffic (v2.13.2) — all succeed ===

# Solo users hit all endpoints
for user in SOLO_USERS:
    for ep in ENDPOINTS:
        success_request(next_timestamp(), "GET", ep, user, "v2.13.2")

# Org users hit all endpoints (including billing — works pre-deploy)
for user_id, org_id in ORG_USERS:
    for ep in ENDPOINTS:
        success_request(next_timestamp(), "GET", ep, user_id, "v2.13.2", org_id)


# === Deploy event ===

minute_counter = 105  # 09:45
emit({
    "@timestamp": next_timestamp(),
    "event_type": "deployment",
    "deploy_version": "v2.14.0-rc3",
    "message": "Deployment v2.14.0-rc3 rolled out to all API gateway instances",
    "deployed_by": "ci-pipeline",
    "changelog": "Updated authorization middleware, billing API rate limits, user profile caching",
})


# === Post-deploy traffic (v2.14.0-rc3) — regression appears ===

minute_counter = 110  # 09:50

# Solo users hit all endpoints — still all succeed
for user in SOLO_USERS:
    for ep in ENDPOINTS:
        success_request(next_timestamp(), "GET", ep, user, "v2.14.0-rc3")

# Extra solo user billing requests to emphasize billing works for solo accounts
for user in SOLO_USERS:
    success_request(next_timestamp(), "GET", "/api/v3/billing/invoices", user, "v2.14.0-rc3")

# Org users on non-billing endpoints — still succeed
for user_id, org_id in ORG_USERS:
    for ep in NON_BILLING:
        success_request(next_timestamp(), "GET", ep, user_id, "v2.14.0-rc3", org_id)

# Org users on billing endpoint — 403 AUTHZ-4821 (3 attempts each, simulating retries)
for user_id, org_id in ORG_USERS:
    for _ in range(3):
        forbidden_request(next_timestamp(), "GET", "/api/v3/billing/invoices", user_id, "v2.14.0-rc3", org_id)
