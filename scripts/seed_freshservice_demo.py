#!/usr/bin/env python3
"""Seed a Freshservice instance with rich, realistic ITSM demo data.

The centerpiece is a "failure caused by a change" scenario that HolmesGPT can
root-cause:

* A standard change tuned PostgreSQL parameters on payment-db-01 during a
  Friday maintenance window. The rollout plan contains a typo: max_connections
  was set to 20 instead of 200.
* Since Monday morning, several incidents report checkout 502s, payment API
  500s, stuck orders and failed refund jobs. Agent notes capture the telltale
  "remaining connection slots are reserved" / "too many clients already"
  database errors.
* A problem ticket tracks the recurring 5xx errors; its cause is deliberately
  left as "under investigation" so an AI investigator has to connect the
  incidents to the change.
* A knowledge base runbook documents the expected max_connections baseline
  (200), providing the breadcrumb that makes the config typo discoverable.

Surrounding the scenario is general-purpose data: departments, locations,
agent groups, requesters, unrelated tickets, noise changes, a release, KB
articles and service requests placed against the default service catalog.

Usage:
    FRESHSERVICE_URL=https://yourdomain.freshservice.com \
    FRESHSERVICE_API_KEY=xxx \
    python scripts/seed_freshservice_demo.py

Also accepts FRESHWORK_URL / FRESHWORK_API_KEY as fallback env var names.

The script is idempotent: it looks records up by name/email/subject before
creating them, so re-running it will not duplicate data.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

BASE_URL = (
    os.environ.get("FRESHSERVICE_URL") or os.environ.get("FRESHWORK_URL") or ""
).rstrip("/")
API_KEY = os.environ.get("FRESHSERVICE_API_KEY") or os.environ.get(
    "FRESHWORK_API_KEY"
)

if not BASE_URL or not API_KEY:
    print(
        "Set FRESHSERVICE_URL and FRESHSERVICE_API_KEY (or FRESHWORK_URL / FRESHWORK_API_KEY)"
    )
    sys.exit(1)

SESSION = requests.Session()
SESSION.auth = (API_KEY, "X")
SESSION.headers.update({"Content-Type": "application/json"})

NOW = datetime.now(timezone.utc)


def last_friday_at(hour: int, minute: int = 0) -> datetime:
    """Return the most recent Friday before today at the given UTC time."""
    days_back = (NOW.weekday() - 4) % 7 or 7
    d = (NOW - timedelta(days=days_back)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return d


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def api(method: str, path: str, payload: Optional[dict] = None, ok=(200, 201)) -> Any:
    url = f"{BASE_URL}/api/v2/{path.lstrip('/')}"
    for attempt in range(5):
        resp = SESSION.request(method, url, json=payload, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "10"))
            print(f"  rate limited, sleeping {wait}s ...")
            time.sleep(wait)
            continue
        if resp.status_code not in ok:
            raise RuntimeError(
                f"{method} {url} -> {resp.status_code}: {resp.text[:500]}"
            )
        if resp.text:
            return resp.json()
        return None
    raise RuntimeError(f"{method} {url} kept hitting rate limits")


def get_all(path: str, key: str, params: str = "") -> List[dict]:
    """Fetch all pages of a list endpoint."""
    results: List[dict] = []
    page = 1
    while True:
        query = f"{path}?{params}page={page}&per_page=100"
        data = api("GET", query)
        items = data.get(key, []) if isinstance(data, dict) else []
        results.extend(items)
        if len(items) < 100:
            return results
        page += 1


def find_by(items: List[dict], field: str, value: str) -> Optional[dict]:
    for item in items:
        if (item.get(field) or "").strip().lower() == value.strip().lower():
            return item
    return None


def ensure(path: str, key: str, match_field: str, payload: dict) -> dict:
    """Create a record unless one with the same match_field value exists."""
    existing = find_by(get_all(path, key), match_field, payload[match_field])
    if existing:
        print(f"  = {key[:-1] if key.endswith('s') else key} exists: {payload[match_field]}")
        return existing
    created = api("POST", path, payload)
    record = created.get(key.rstrip("s")) or next(iter(created.values()))
    print(f"  + created {path}: {payload.get(match_field)}")
    time.sleep(0.3)
    return record


# ---------------------------------------------------------------------------
# 1. Foundation: departments, locations, groups, requesters
# ---------------------------------------------------------------------------

def seed_foundation() -> Dict[str, Any]:
    print("\n== Departments ==")
    departments = {
        name: ensure(
            "departments", "departments", "name", {"name": name, "description": desc}
        )
        for name, desc in [
            ("IT Operations", "Infrastructure, networking and internal IT support"),
            ("Engineering", "Product engineering and platform teams"),
            ("Finance", "Accounting, payroll and financial planning"),
            ("Sales", "Sales and account management"),
        ]
    }

    print("\n== Locations ==")
    locations = {
        name: ensure(
            "locations",
            "locations",
            "name",
            {"name": name, "address": addr},
        )
        for name, addr in [
            (
                "Tel Aviv HQ",
                {"line1": "10 Menachem Begin Rd", "city": "Tel Aviv", "country": "Israel"},
            ),
            (
                "London Office",
                {"line1": "1 Finsbury Ave", "city": "London", "country": "United Kingdom"},
            ),
        ]
    }

    print("\n== Agent groups ==")
    groups = {
        name: ensure(
            "groups", "groups", "name", {"name": name, "description": desc}
        )
        for name, desc in [
            ("IT Support", "First-line IT support for employees"),
            ("Platform Engineering", "Owns databases, Kubernetes and internal platform services"),
            ("Facilities", "Office facilities and physical infrastructure"),
        ]
    }

    # Changes can only be assigned to agents who are members of the change's
    # group, so add the API user's agent to every demo group.
    agents = get_all("agents", "agents")
    agent_id = agents[0]["id"] if agents else None
    if agent_id:
        for group in groups.values():
            members = group.get("members") or []
            if agent_id not in members:
                api("PUT", f"groups/{group['id']}", {"members": members + [agent_id]})
                time.sleep(0.3)

    print("\n== Requesters ==")
    requester_specs = [
        ("Maya", "Cohen", "maya.cohen@demo.robustalabs.dev", "Finance", "Financial Analyst", "Tel Aviv HQ"),
        ("Noa", "Peretz", "noa.peretz@demo.robustalabs.dev", "Finance", "Controller", "Tel Aviv HQ"),
        ("Daniel", "Katz", "daniel.katz@demo.robustalabs.dev", "Sales", "Account Executive", "Tel Aviv HQ"),
        ("Emma", "Johnson", "emma.johnson@demo.robustalabs.dev", "Sales", "Sales Manager", "London Office"),
        ("Sarah", "Mizrahi", "sarah.mizrahi@demo.robustalabs.dev", "Engineering", "Backend Developer", "Tel Aviv HQ"),
        ("James", "Wong", "james.wong@demo.robustalabs.dev", "Engineering", "Site Reliability Engineer", "London Office"),
        ("Priya", "Sharma", "priya.sharma@demo.robustalabs.dev", "Engineering", "Data Engineer", "London Office"),
        ("Liam", "OBrien", "liam.obrien@demo.robustalabs.dev", "IT Operations", "Facilities Coordinator", "Tel Aviv HQ"),
    ]
    all_requesters = get_all("requesters", "requesters")
    requesters = {}
    for first, last, email, dept, title, loc in requester_specs:
        existing = find_by(all_requesters, "primary_email", email)
        if existing:
            print(f"  = requester exists: {email}")
            requesters[email] = existing
            continue
        requesters[email] = api(
            "POST",
            "requesters",
            {
                "first_name": first,
                "last_name": last,
                "primary_email": email,
                "job_title": title,
                "department_ids": [departments[dept]["id"]],
                "location_id": locations[loc]["id"],
            },
        )["requester"]
        print(f"  + created requester: {email}")
        time.sleep(0.3)

    return {
        "departments": departments,
        "locations": locations,
        "groups": groups,
        "requesters": requesters,
        "agent_id": agent_id,
    }


# ---------------------------------------------------------------------------
# 1b. ITAM: assets and devices (newer /api/v2/itam API, Device42-based)
# ---------------------------------------------------------------------------
#
# Unlike the classic API, itam create/update/delete URLs need a trailing
# slash (POST without it silently acts as a list call), asset creation does
# NOT upsert by name, and the create response is Device42-style:
# {"code": 0, "msg": ["asset added/edited.", <id>, <name>, true, true]}.

def seed_itam(ctx: Dict[str, Any]) -> None:
    print("\n== ITAM assets ==")
    try:
        existing_assets = get_all("itam/assets", "assets")
    except RuntimeError as e:
        print(f"  ! ITAM API unavailable, skipping assets/devices: {e}")
        return

    departments = ctx["departments"]
    locations = ctx["locations"]
    requesters = ctx["requesters"]

    def dept(name: str) -> Optional[int]:
        return departments[name]["id"] if name in departments else None

    def loc(name: str) -> Optional[int]:
        return locations[name]["id"] if name in locations else None

    def user(email: str) -> Optional[int]:
        r = requesters.get(email)
        return r["id"] if r else None

    asset_specs: List[dict] = [
        # The payments stack - payment-db-01 is the host the culprit change
        # (max_connections typo) was applied to. Notes stay neutral: specs and
        # ownership only, the KB runbook holds the config baseline.
        {
            "name": "payment-db-01",
            "type": "Server",
            "serial_no": "DL380-2023-04417",
            "asset_no": "AST-SRV-0001",
            "impact": "High",
            "state": "In Use",
            "notes": (
                "Primary PostgreSQL 15 node for the payments stack (orders, payments, "
                "refunds databases). Runs postgres and pgbouncer 1.21. HPE ProLiant "
                "DL380 Gen11, 64GB RAM, 2x1.92TB NVMe RAID1. Owned by Platform "
                "Engineering. Streaming replication to payment-db-02."
            ),
            "department_id": dept("Engineering"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "payment-db-02",
            "type": "Server",
            "serial_no": "DL380-2023-04418",
            "asset_no": "AST-SRV-0002",
            "impact": "High",
            "state": "In Use",
            "notes": (
                "Hot-standby PostgreSQL 15 replica of payment-db-01 (streaming "
                "replication, async). HPE ProLiant DL380 Gen11, 64GB RAM. Owned by "
                "Platform Engineering."
            ),
            "department_id": dept("Engineering"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "checkout-web-01",
            "type": "Cloud Instance",
            "asset_no": "AST-CLD-0001",
            "impact": "Medium",
            "state": "In Use",
            "notes": (
                "AWS EC2 c6i.xlarge (eu-central-1a) serving the storefront checkout "
                "frontend behind the public ALB. Auto Scaling group checkout-web."
            ),
            "department_id": dept("Engineering"),
        },
        {
            "name": "checkout-web-02",
            "type": "Cloud Instance",
            "asset_no": "AST-CLD-0002",
            "impact": "Medium",
            "state": "In Use",
            "notes": (
                "AWS EC2 c6i.xlarge (eu-central-1b) serving the storefront checkout "
                "frontend behind the public ALB. Auto Scaling group checkout-web."
            ),
            "department_id": dept("Engineering"),
        },
        {
            "name": "payment-api-01",
            "type": "Cloud Instance",
            "asset_no": "AST-CLD-0003",
            "impact": "High",
            "state": "In Use",
            "notes": (
                "AWS EC2 m6i.large running the payment-api service (Django). "
                "Connects to payment-db-01 through pgbouncer. Handles card capture "
                "and refunds via the PSP."
            ),
            "department_id": dept("Engineering"),
        },
        {
            "name": "core-switch-tlv-01",
            "type": "Switch",
            "serial_no": "FDO27110QBX",
            "asset_no": "AST-NET-0001",
            "impact": "High",
            "state": "In Use",
            "notes": "Cisco Catalyst 9300 48-port core switch, Tel Aviv HQ server room rack A1.",
            "department_id": dept("IT Operations"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "edge-fw-tlv-01",
            "type": "Firewall",
            "serial_no": "PA-3410-00981",
            "asset_no": "AST-NET-0002",
            "impact": "High",
            "state": "In Use",
            "notes": "Palo Alto PA-3410 edge firewall, Tel Aviv HQ. HA pair peer edge-fw-tlv-02 (planned).",
            "department_id": dept("IT Operations"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "backup-nas-01",
            "type": "Storage",
            "serial_no": "SYN-RS4021-7742",
            "asset_no": "AST-STO-0001",
            "impact": "Medium",
            "state": "In Use",
            "notes": "Synology RS4021xs+ backup target for nightly database dumps and office file shares. 96TB raw.",
            "department_id": dept("IT Operations"),
            "location_id": loc("Tel Aviv HQ"),
        },
        # End-user hardware tied to real requesters
        {
            "name": "MBP14-M3-0117",
            "type": "Laptop",
            "serial_no": "C02ZK1ANMD6T",
            "asset_no": "AST-LAP-0117",
            "impact": "Low",
            "state": "In Use",
            "notes": 'MacBook Pro 14" M3 Pro, 18GB/512GB. Assigned to Sarah Mizrahi (Backend Developer).',
            "user_id": user("sarah.mizrahi@demo.robustalabs.dev"),
            "department_id": dept("Engineering"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "TP-X1C-0242",
            "type": "Laptop",
            "serial_no": "PF-4XJTQ9",
            "asset_no": "AST-LAP-0242",
            "impact": "Low",
            "state": "In Use",
            "notes": "Lenovo ThinkPad X1 Carbon Gen 12, 32GB/1TB. Assigned to Maya Cohen (Financial Analyst).",
            "user_id": user("maya.cohen@demo.robustalabs.dev"),
            "department_id": dept("Finance"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "MBP16-M3-0305",
            "type": "Laptop",
            "serial_no": "C02WV3PLQ05N",
            "asset_no": "AST-LAP-0305",
            "impact": "Low",
            "state": "In Use",
            "notes": 'MacBook Pro 16" M3 Max, 36GB/1TB. Assigned to James Wong (Site Reliability Engineer).',
            "user_id": user("james.wong@demo.robustalabs.dev"),
            "department_id": dept("Engineering"),
            "location_id": loc("London Office"),
        },
        {
            "name": "DELL-LAT-0418",
            "type": "Laptop",
            "serial_no": "8Y2VJ34",
            "asset_no": "AST-LAP-0418",
            "impact": "Low",
            "state": "In Stock",
            "notes": "Dell Latitude 7450, 16GB/512GB. Spare pool unit held by IT Support for loaners.",
            "department_id": dept("IT Operations"),
            "location_id": loc("Tel Aviv HQ"),
        },
    ]

    for spec in asset_specs:
        payload = {k: v for k, v in spec.items() if v is not None}
        if find_by(existing_assets, "name", spec["name"]):
            print(f"  = asset exists: {spec['name']}")
            continue
        # itam asset creation needs the trailing slash and does not upsert
        api("POST", "itam/assets/", payload)
        print(f"  + created asset: {spec['name']} ({spec['type']})")
        time.sleep(0.3)

    print("\n== ITAM devices ==")
    # Devices are the discovery-level view of the server fleet; unlike assets
    # they DO upsert by name, so plain POSTs are already idempotent.
    device_specs = [
        {"name": "payment-db-01", "os": "Ubuntu", "osver": "22.04 LTS"},
        {"name": "payment-db-02", "os": "Ubuntu", "osver": "22.04 LTS"},
        {"name": "checkout-web-01", "os": "Amazon Linux", "osver": "2023"},
        {"name": "checkout-web-02", "os": "Amazon Linux", "osver": "2023"},
        {"name": "payment-api-01", "os": "Amazon Linux", "osver": "2023"},
    ]
    for spec in device_specs:
        try:
            api("POST", "itam/devices/", spec)
            print(f"  + upserted device: {spec['name']}")
        except RuntimeError:
            # some optional fields may be rejected depending on plan; retry bare
            api("POST", "itam/devices/", {"name": spec["name"]})
            print(f"  + upserted device (name only): {spec['name']}")
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# 2. Knowledge base
# ---------------------------------------------------------------------------

def seed_knowledge_base() -> None:
    print("\n== Knowledge base ==")
    categories = get_all("solutions/categories", "categories")
    category = find_by(categories, "name", "IT Knowledge Base")
    if not category:
        category = api(
            "POST",
            "solutions/categories",
            {"name": "IT Knowledge Base", "description": "How-tos and runbooks"},
        )["category"]
        print("  + created category: IT Knowledge Base")

    folders = get_all("solutions/folders", "folders", params=f"category_id={category['id']}&")
    folder_specs = [
        ("How-To Guides", "Self-service guides for employees"),
        ("Runbooks", "Operational runbooks for the IT and platform teams"),
    ]
    folder_ids = {}
    for name, desc in folder_specs:
        folder = find_by(folders, "name", name)
        if not folder:
            folder = api(
                "POST",
                "solutions/folders",
                {
                    "name": name,
                    "description": desc,
                    "category_id": category["id"],
                    "visibility": 1,
                },
            )["folder"]
            print(f"  + created folder: {name}")
        folder_ids[name] = folder["id"]

    articles = [
        (
            "How-To Guides",
            "Troubleshooting VPN connectivity issues",
            """<p>If your VPN connection keeps dropping:</p>
<ol><li>Confirm you are on the latest GlobalConnect client (v6.2 or newer).</li>
<li>Switch from UDP to TCP in the client settings if you are on hotel/café Wi-Fi.</li>
<li>macOS 15.2 users: a known bug drops the tunnel every ~30 minutes. The fix is tracked as a known error; the workaround is to disable 'hardware acceleration' in the client.</li>
<li>If problems persist, open a ticket with the IT Support group and attach the client logs.</li></ol>""",
        ),
        (
            "How-To Guides",
            "How to request a new laptop",
            """<p>New laptops are requested through the service catalog item <b>New employee laptop</b>.
Standard models: MacBook Pro 14 (engineering), ThinkPad X1 Carbon (all other roles).
Approval is required from your manager and provisioning takes 3-5 business days.</p>""",
        ),
        (
            "How-To Guides",
            "Submitting expense reports",
            """<p>Expenses are submitted in NetSuite under Employee Center &gt; Expense Reports.
Attach receipts for any single expense over $25. Finance processes reports every Tuesday.</p>""",
        ),
        (
            "Runbooks",
            "Payment platform runbook: common database errors",
            """<p>This runbook covers the payments stack: <b>checkout-web → payment-api → pgbouncer → payment-db-01 (PostgreSQL 15)</b>.</p>
<h3>Error: FATAL: remaining connection slots are reserved for non-replication superuser connections</h3>
<p>PostgreSQL has run out of client connection slots. Check:</p>
<ol><li><code>SELECT count(*) FROM pg_stat_activity;</code> vs the configured <code>max_connections</code>.</li>
<li>The capacity baseline for payment-db-01 is <b>max_connections = 200</b> (sized for peak checkout traffic plus batch jobs). Any lower value will exhaust connections during business hours.</li>
<li>Recent configuration changes to <code>postgresql.conf</code> — config is managed via change requests, so review recently implemented changes for this host.</li>
<li>Connection leaks in payment-api (look for sessions idle in transaction).</li></ol>
<h3>Error: too many clients already</h3>
<p>Same root cause as above, reported by clients connecting through pgbouncer when the server pool is exhausted.</p>
<h3>Escalation</h3>
<p>Page the Platform Engineering group. Rollback procedure for database config changes: restore the previous postgresql.conf from /etc/postgresql/backup and restart the service.</p>""",
        ),
        (
            "Runbooks",
            "Office network: Wi-Fi troubleshooting",
            """<p>For reports of slow Wi-Fi, check the access point dashboard for channel utilization above 70%.
London office APs were last rebalanced in June. Escalate persistent issues to IT Support with a speed test attached.</p>""",
        ),
    ]

    for folder_name, title, body in articles:
        existing = get_all(
            "solutions/articles", "articles", params=f"folder_id={folder_ids[folder_name]}&"
        )
        if find_by(existing, "title", title):
            print(f"  = article exists: {title}")
            continue
        api(
            "POST",
            "solutions/articles",
            {
                "title": title,
                "description": body,
                "folder_id": folder_ids[folder_name],
                "status": 2,  # published
            },
        )
        print(f"  + created article: {title}")
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# 3. Changes and release (the root-cause scenario lives here)
# ---------------------------------------------------------------------------

def seed_changes(ctx: Dict[str, Any]) -> Dict[str, Any]:
    print("\n== Changes ==")
    requesters = ctx["requesters"]
    groups = ctx["groups"]
    james = requesters["james.wong@demo.robustalabs.dev"]
    sarah = requesters["sarah.mizrahi@demo.robustalabs.dev"]
    liam = requesters["liam.obrien@demo.robustalabs.dev"]

    window_start = last_friday_at(18, 0)
    window_end = last_friday_at(19, 0)

    existing_changes = get_all("changes", "changes")

    def ensure_change(payload: dict, notes: Optional[List[str]] = None) -> dict:
        found = find_by(existing_changes, "subject", payload["subject"])
        if found:
            print(f"  = change exists: {payload['subject']}")
            return found
        # The default change stateflow only allows: open (1) -> pending release
        # (4) -> pending review (5) -> closed (6). Moving past "open" requires
        # an assigned agent (who must be a group member) and planning fields.
        target_status = payload.pop("status", 1)
        change = api("POST", "changes", payload)["change"]
        print(f"  + created change CHN-{change['id']}: {payload['subject']}")
        if ctx.get("agent_id"):
            api("PUT", f"changes/{change['id']}", {"agent_id": ctx["agent_id"]})
            time.sleep(0.3)
        for status in [s for s in (4, 5, 6) if s <= target_status]:
            try:
                api("PUT", f"changes/{change['id']}", {"status": status})
                time.sleep(0.3)
            except RuntimeError as exc:
                print(f"    ! could not move change to status {status}: {exc}")
                break
        for note in notes or []:
            api("POST", f"changes/{change['id']}/notes", {"body": note})
            time.sleep(0.3)
        return change

    # The culprit change. The rollout plan sets max_connections to 20 —
    # a typo for 200 — which exhausts DB connections once weekday traffic
    # returns. The change itself looks routine and "successful".
    culprit = ensure_change(
        {
            "subject": "Apply PostgreSQL tuning parameters on payment-db-01",
            "description": (
                "<p>Quarterly database maintenance for the payments stack. "
                "Apply the reviewed postgresql.conf tuning set on payment-db-01 to "
                "reduce memory pressure observed during June peak.</p>"
            ),
            "requester_id": james["id"],
            "group_id": groups["Platform Engineering"]["id"],
            "priority": 2,
            "impact": 2,
            "risk": 2,
            "change_type": 2,  # standard
            "status": 5,  # pending review (implemented)
            "planned_start_date": iso(window_start),
            "planned_end_date": iso(window_end),
            "planning_fields": {
                "reason_for_change": {
                    "description": "<p>June capacity review flagged elevated memory usage on payment-db-01. DBA team proposed a tuning pass on postgresql.conf.</p>"
                },
                "change_impact": {
                    "description": "<p>Brief PostgreSQL restart (~30s) during the Friday evening maintenance window. Payments traffic is minimal at that time.</p>"
                },
                "rollout_plan": {
                    "description": (
                        "<p>1. Snapshot current postgresql.conf to /etc/postgresql/backup.<br>"
                        "2. Apply new parameters: shared_buffers=8GB, work_mem=64MB, "
                        "effective_cache_size=24GB, max_connections=20.<br>"
                        "3. Restart PostgreSQL via systemctl.<br>"
                        "4. Run smoke tests: checkout flow, refund job dry-run.</p>"
                    )
                },
                "backout_plan": {
                    "description": "<p>Restore previous postgresql.conf from /etc/postgresql/backup and restart PostgreSQL.</p>"
                },
            },
        },
        notes=[
            (
                "<p>Implementation complete. Config applied and PostgreSQL restarted at "
                f"{window_start.strftime('%Y-%m-%d %H:%M')} UTC. Smoke tests passed: one checkout "
                "transaction and a refund dry-run both succeeded. Closing the window early.</p>"
            )
        ],
    )

    noise1 = ensure_change(
        {
            "subject": "Upgrade conference room AV firmware (Tel Aviv HQ)",
            "description": "<p>Vendor firmware upgrade for the Poly units in all Tel Aviv meeting rooms.</p>",
            "requester_id": liam["id"],
            "group_id": groups["Facilities"]["id"],
            "priority": 1,
            "impact": 1,
            "risk": 1,
            "change_type": 2,
            "status": 6,  # closed
            "planned_start_date": iso(window_start - timedelta(days=3)),
            "planned_end_date": iso(window_end - timedelta(days=3)),
            "planning_fields": {
                "reason_for_change": {
                    "description": "<p>Vendor firmware update fixing HDMI handshake issues.</p>"
                },
                "change_impact": {
                    "description": "<p>Meeting rooms unavailable for ~15 minutes each.</p>"
                },
                "rollout_plan": {
                    "description": "<p>Apply vendor OTA update per room, verify test call.</p>"
                },
                "backout_plan": {
                    "description": "<p>Reflash previous firmware from USB image.</p>"
                },
            },
        }
    )

    noise2 = ensure_change(
        {
            "subject": "Enforce MFA on the VPN gateway",
            "description": "<p>Security hardening: require MFA for all VPN logins. Pending CAB approval.</p>",
            "requester_id": sarah["id"],
            "group_id": groups["IT Support"]["id"],
            "priority": 2,
            "impact": 2,
            "risk": 2,
            "change_type": 3,  # major -> goes through CAB approval
            "status": 1,  # open (approval statuses cannot be set via the API stateflow)
            "planned_start_date": iso(NOW + timedelta(days=7)),
            "planned_end_date": iso(NOW + timedelta(days=7, hours=2)),
        }
    )

    print("\n== Release ==")
    releases = get_all("releases", "releases")
    if not find_by(releases, "subject", "July infrastructure maintenance"):
        release = api(
            "POST",
            "releases",
            {
                "subject": "July infrastructure maintenance",
                "description": (
                    "<p>Bundled infrastructure maintenance for July: PostgreSQL tuning on "
                    f"payment-db-01 (change CHN-{culprit['id']}) and VPN gateway MFA rollout "
                    f"(change CHN-{noise2['id']}).</p>"
                ),
                "priority": 2,
                "status": 1,  # open; moved to "in progress" below
                "release_type": 2,
                "planned_start_date": iso(window_start),
                "planned_end_date": iso(NOW + timedelta(days=10)),
            },
        )["release"]
        try:
            api("PUT", f"releases/{release['id']}", {"status": 3})  # in progress
        except RuntimeError as exc:
            print(f"    ! could not move release to in-progress: {exc}")
        print("  + created release: July infrastructure maintenance")
    else:
        print("  = release exists: July infrastructure maintenance")

    return {"culprit": culprit, "noise1": noise1, "noise2": noise2}


# ---------------------------------------------------------------------------
# 4. Tickets (incidents) and problem records
# ---------------------------------------------------------------------------

def seed_tickets(ctx: Dict[str, Any], changes: Dict[str, Any]) -> None:
    print("\n== Tickets ==")
    groups = ctx["groups"]
    monday_morning = last_friday_at(18, 0) + timedelta(days=3) - timedelta(hours=9)

    existing = get_all("tickets", "tickets")

    # urgency/impact pairs that make the default priority matrix compute the
    # intended priority (low, medium, high, urgent).
    priority_matrix = {1: (1, 1), 2: (2, 2), 3: (2, 3), 4: (3, 3)}

    def ensure_ticket(payload: dict, notes: Optional[List[dict]] = None) -> Optional[dict]:
        found = find_by(existing, "subject", payload["subject"])
        if found:
            print(f"  = ticket exists: {payload['subject']}")
            return found
        urgency, impact = priority_matrix[payload.get("priority", 1)]
        payload.setdefault("urgency", urgency)
        payload.setdefault("impact", impact)
        ticket = api("POST", "tickets", payload)["ticket"]
        print(f"  + created ticket #{ticket['id']}: {payload['subject']}")
        for note in notes or []:
            api("POST", f"tickets/{ticket['id']}/notes", note)
            time.sleep(0.3)
        time.sleep(0.3)
        return ticket

    # Freshservice recomputes ticket priority from urgency and impact via the
    # priority matrix, so every payload must carry urgency/impact values that
    # map to the intended priority (see PRIORITY_MATRIX below).
    payment_incidents = [
        (
            {
                "subject": "Checkout page times out and shows a 502 error",
                "description": (
                    "<p>Since this morning multiple customers report that the checkout page "
                    "spins for ~30 seconds and then shows a 502 Bad Gateway. Several deals "
                    "are blocked. It was working fine last week.</p>"
                ),
                "email": "daniel.katz@demo.robustalabs.dev",
                "priority": 4,
                "status": 2,
                "source": 2,
                "urgency": 3,
                "impact": 3,
                "category": "Software",
                "tags": ["payments", "checkout", "major-incident", "payment-db-01"],
                "group_id": groups["Platform Engineering"]["id"],
            },
            [
                {
                    "body": (
                        "<p>Triage: payment-api pods are healthy but requests to the database hang. "
                        "payment-api logs are full of:<br>"
                        "<code>FATAL: remaining connection slots are reserved for non-replication "
                        "superuser connections</code><br>"
                        "coming from payment-db-01. Error rate started "
                        f"around {monday_morning.strftime('%Y-%m-%d %H:%M')} UTC when EU traffic ramped up. "
                        "Escalating to Platform Engineering as a major incident.</p>"
                    ),
                    "private": True,
                },
            ],
        ),
        (
            {
                "subject": "Payment API returns intermittent 500 errors for EU customers",
                "description": (
                    "<p>Our EU account managers report intermittent payment failures. "
                    "Retrying sometimes works. Started Monday morning, getting worse "
                    "around lunchtime peaks.</p>"
                ),
                "email": "emma.johnson@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 2,
                "urgency": 2,
                "impact": 3,
                "category": "Software",
                "tags": ["payments", "payment-db-01"],
                "group_id": groups["Platform Engineering"]["id"],
            },
            [
                {
                    "body": (
                        "<p>pgbouncer stats show the server pool to payment-db-01 saturating at "
                        "20 connections and queueing clients. Failures line up with traffic peaks. "
                        "Investigating why the ceiling is 20.</p>"
                    ),
                    "private": True,
                },
            ],
        ),
        (
            {
                "subject": "Customer orders stuck in 'pending payment' state",
                "description": (
                    "<p>Finance reconciliation shows a growing queue of orders stuck in "
                    "'pending payment' since Monday. The payment provider dashboard shows no "
                    "outage on their side.</p>"
                ),
                "email": "maya.cohen@demo.robustalabs.dev",
                "priority": 3,
                "status": 3,
                "source": 1,
                "category": "Software",
                "tags": ["payments"],
                "group_id": groups["Platform Engineering"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Nightly refund reconciliation job failed",
                "description": (
                    "<p>The refunds batch job failed last night and the retry failed as well. "
                    "Job log excerpt: <code>could not connect to server: FATAL: sorry, too many "
                    "clients already</code>.</p>"
                ),
                "email": "noa.peretz@demo.robustalabs.dev",
                "priority": 2,
                "status": 2,
                "source": 1,
                "category": "Software",
                "tags": ["payments", "batch-jobs", "payment-db-01"],
                "group_id": groups["Platform Engineering"]["id"],
            },
            None,
        ),
    ]

    noise_tickets = [
        (
            {
                "subject": "VPN disconnects every 30 minutes on macOS",
                "description": "<p>Since updating to macOS 15.2 my VPN drops roughly every half hour and reconnects by itself. Annoying during long deploys.</p>",
                "email": "sarah.mizrahi@demo.robustalabs.dev",
                "priority": 2,
                "status": 2,
                "source": 2,
                "category": "Network",
                "tags": ["vpn"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Cannot print from the 3rd floor printer",
                "description": "<p>The Tel Aviv 3rd floor printer shows 'driver unavailable' from Windows laptops. Mac users can print fine.</p>",
                "email": "liam.obrien@demo.robustalabs.dev",
                "priority": 1,
                "status": 3,
                "source": 3,
                "category": "Hardware",
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "NetSuite password reset needed",
                "description": "<p>I'm locked out of NetSuite after three wrong attempts. Need a reset before the Tuesday expense run.</p>",
                "email": "noa.peretz@demo.robustalabs.dev",
                "priority": 2,
                "status": 4,
                "source": 1,
                "category": "Software",
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Wi-Fi very slow in the London office",
                "description": "<p>Downloads crawl at under 1 Mbps near the east meeting rooms. Speed test attached. Fine elsewhere on the floor.</p>",
                "email": "emma.johnson@demo.robustalabs.dev",
                "priority": 1,
                "status": 2,
                "source": 2,
                "category": "Network",
                "tags": ["wifi", "london"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Access request: Grafana dashboards for the data team",
                "description": "<p>Please grant the data engineering team viewer access to the payments Grafana folder for capacity planning work.</p>",
                "email": "priya.sharma@demo.robustalabs.dev",
                "priority": 1,
                "status": 2,
                "source": 2,
                "category": "Software",
                "tags": ["access-request"],
                "group_id": groups["Platform Engineering"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Laptop battery is swelling",
                "description": "<p>The battery on my MacBook is visibly swelling and the trackpad no longer clicks. Stopped using it as a precaution.</p>",
                "email": "daniel.katz@demo.robustalabs.dev",
                "priority": 4,
                "status": 4,
                "source": 2,
                "category": "Hardware",
                "group_id": groups["IT Support"]["id"],
            },
            [
                {
                    "body": "<p>Replacement device issued from spares, battery unit sent for safe disposal. Resolving.</p>",
                    "private": False,
                }
            ],
        ),
        (
            {
                "subject": "Quarterly audit evidence export failing",
                "description": "<p>The compliance export from the audit tool times out after 10 minutes. We need this for the Q3 audit due end of week.</p>",
                "email": "noa.peretz@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 1,
                "category": "Software",
                "due_by": iso(NOW + timedelta(hours=4)),
                "fr_due_by": iso(NOW + timedelta(hours=2)),
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
    ]

    for payload, notes in payment_incidents + noise_tickets:
        ensure_ticket(payload, notes)

    print("\n== Problems ==")
    existing_problems = get_all("problems", "problems")

    def ensure_problem(payload: dict, notes: Optional[List[str]] = None) -> Optional[dict]:
        found = find_by(existing_problems, "subject", payload["subject"])
        if found:
            print(f"  = problem exists: {payload['subject']}")
            return found
        problem = api("POST", "problems", payload)["problem"]
        print(f"  + created problem PRB-{problem['id']}: {payload['subject']}")
        for note in notes or []:
            api("POST", f"problems/{problem['id']}/notes", {"body": note})
            time.sleep(0.3)
        return problem

    james = ctx["requesters"]["james.wong@demo.robustalabs.dev"]
    sarah = ctx["requesters"]["sarah.mizrahi@demo.robustalabs.dev"]

    ensure_problem(
        {
            "subject": "Recurring payment gateway 5xx errors during peak traffic",
            "description": (
                "<p>Tracking problem for the payment failures reported since Monday morning: "
                "checkout 502s, intermittent payment API 500s, orders stuck in 'pending payment' "
                "and the failed refund reconciliation job. All symptoms point at database "
                "connectivity to payment-db-01.</p>"
            ),
            "requester_id": james["id"],
            "group_id": groups["Platform Engineering"]["id"],
            "priority": 4,
            "status": 1,
            "impact": 3,
            "known_error": False,
            "due_by": iso(NOW + timedelta(days=7)),
            "analysis_fields": {
                "problem_symptom": {
                    "description": (
                        "<p>Payment-api requests hang and fail with database errors "
                        "('remaining connection slots are reserved', 'too many clients already') "
                        "whenever concurrent traffic rises. Off-peak requests succeed.</p>"
                    )
                },
                "problem_impact": {
                    "description": "<p>Roughly 15% of checkout attempts fail during peak hours. Revenue-impacting; refunds batch is also blocked.</p>"
                },
                "problem_cause": {
                    "description": "<p>Under investigation. Connection exhaustion on payment-db-01 — root cause not yet confirmed.</p>"
                },
            },
        },
        notes=[
            "<p>Correlation so far: symptoms began the first business morning after the Friday "
            "maintenance window. No deploys of payment-api since Thursday. Reviewing recent "
            "infrastructure changes next.</p>"
        ],
    )

    ensure_problem(
        {
            "subject": "VPN client drops on macOS 15.2",
            "description": "<p>Multiple users on macOS 15.2 report the VPN tunnel dropping every ~30 minutes. Vendor case #48211 open.</p>",
            "requester_id": sarah["id"],
            "group_id": groups["IT Support"]["id"],
            "priority": 2,
            "status": 1,
            "impact": 1,
            "known_error": True,
            "due_by": iso(NOW + timedelta(days=14)),
            "analysis_fields": {
                "problem_symptom": {
                    "description": "<p>Tunnel drops and auto-reconnects every ~30 minutes on macOS 15.2 with client v6.1.</p>"
                },
                "problem_impact": {
                    "description": "<p>Interrupted SSH sessions and deploys for ~12 engineering users.</p>"
                },
                "problem_cause": {
                    "description": "<p>Vendor confirmed a keepalive bug in client v6.1 hardware acceleration path. Fixed in v6.2.</p>"
                },
            },
        },
        notes=["<p>Workaround published in the KB: disable hardware acceleration or upgrade to client v6.2.</p>"],
    )


# ---------------------------------------------------------------------------
# 5. Service requests against the default catalog
# ---------------------------------------------------------------------------

def seed_service_requests() -> None:
    print("\n== Service requests ==")
    items = get_all("service_catalog/items", "service_items")
    if not items:
        print("  ! no service catalog items available, skipping")
        return
    wanted = [
        ("laptop", "priya.sharma@demo.robustalabs.dev", "Priya Sharma"),
        ("laptop", "daniel.katz@demo.robustalabs.dev", "Daniel Katz"),
    ]
    existing_tickets = get_all("tickets", "tickets")
    # Fall back to the first item if no laptop-ish item exists
    for keyword, email, name in wanted:
        item = next(
            (i for i in items if keyword in (i.get("name") or "").lower()),
            items[0],
        )
        # place_request is not idempotent; skip if a matching request exists
        # (service request subjects look like "Request for <name> : <item>")
        if any(
            name in (t.get("subject") or "") and item["name"] in (t.get("subject") or "")
            for t in existing_tickets
        ):
            print(f"  = service request exists: {item['name']} for {email}")
            continue
        try:
            api(
                "POST",
                f"service_catalog/items/{item['display_id']}/place_request",
                {"email": email, "quantity": 1},
            )
            print(f"  + placed request '{item['name']}' for {email}")
            time.sleep(0.3)
        except RuntimeError as exc:
            print(f"  ! could not place request for '{item['name']}': {exc}")


def main() -> None:
    print(f"Seeding Freshservice demo data at {BASE_URL}")
    ctx = seed_foundation()
    seed_itam(ctx)
    seed_knowledge_base()
    changes = seed_changes(ctx)
    seed_tickets(ctx, changes)
    seed_service_requests()
    print("\nDone. The 'failure caused by a change' scenario is ready:")
    print(" - Culprit change: 'Apply PostgreSQL tuning parameters on payment-db-01'")
    print(" - Ask Holmes: 'Customers report checkout failures since Monday - find the root cause using Freshservice'")


if __name__ == "__main__":
    main()
