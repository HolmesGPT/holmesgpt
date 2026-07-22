#!/usr/bin/env python3
"""Seed Freshservice with three IT-operations demo scenarios for HolmesGPT.

Each scenario starts from a monitoring alert (see fire_alert.py), lets Holmes
investigate using Freshservice data (tickets, changes, releases, ITAM assets,
knowledge base, announcements) and ends with Holmes opening a ticket that
contains the analysis.

Scenario A -- "The Friday-Night Firewall" (root cause: a change)
    A PAN-OS firmware upgrade on the London edge firewall last night broke
    every GlobalConnect SSL-VPN tunnel. London users file tickets; a vendor
    known-issue KB article ties PAN-OS 11.2.3 to the failure; the announcement
    and the change record pin the timeline. Holmes should identify change
    CHN-x as the cause and recommend the documented rollback.

Scenario B -- "Patch Tuesday Strikes Back" (root cause: a change + asset pattern)
    A Windows cumulative update deployed to deployment ring 2 boot-loops every
    ThinkPad X1 Carbon Gen 12 on firmware < 1.42. The affected users' tickets
    only make sense once correlated with their ITAM laptop assets: all broken
    devices are the same model (a Gen 12 on firmware 1.42 survived). Ring 3 --
    200 more laptops -- is scheduled for tomorrow as a release record. Holmes
    should find the model/firmware pattern and recommend halting ring 3.

Scenario C -- "The Silent Expiry" (root cause: a process failure, not a change)
    The SignFlow e-signature SaaS suspended the account because the
    subscription expired: the renewal was quoted three weeks ago but the
    renewal change request is still stuck awaiting CAB approval, and the
    warning ticket went unanswered. No infrastructure change correlates.
    Holmes should reconstruct the paper trail and recommend the emergency
    reinstatement runbook plus expediting the approval.

Usage:
    FRESHSERVICE_URL=https://yourdomain.freshservice.com \
    FRESHSERVICE_API_KEY=xxx \
    python scripts/freshservice_demo/seed_it_scenarios.py

Also accepts FRESHWORK_URL / FRESHWORK_API_KEY as fallback env var names.

The script is idempotent: records are looked up by name/email/subject before
creation, so re-running it will not duplicate data.
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
YESTERDAY_EVENING = (NOW - timedelta(days=1)).replace(
    hour=21, minute=0, second=0, microsecond=0
)
THIS_MORNING = NOW.replace(hour=7, minute=0, second=0, microsecond=0)
TOMORROW_MORNING = THIS_MORNING + timedelta(days=1)
SIGNFLOW_EXPIRY = (NOW - timedelta(days=2)).date()
SIGNFLOW_QUOTE_DATE = (NOW - timedelta(days=21)).date()


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def day(d) -> str:
    return d.strftime("%Y-%m-%d")


def api(method: str, path: str, payload: Optional[dict] = None, ok=(200, 201)) -> Any:
    url = f"{BASE_URL}/api/v2/{path.lstrip('/')}"
    for _attempt in range(5):
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
    existing = find_by(get_all(path, key), match_field, payload[match_field])
    if existing:
        print(f"  = exists: {payload[match_field]}")
        return existing
    created = api("POST", path, payload)
    record = created.get(key.rstrip("s")) or next(iter(created.values()))
    print(f"  + created {path}: {payload.get(match_field)}")
    time.sleep(0.3)
    return record


# ---------------------------------------------------------------------------
# Foundation: departments, locations, groups, requesters
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
            ("Legal", "Legal, contracts and compliance"),
        ]
    }

    print("\n== Locations ==")
    locations = {
        name: ensure("locations", "locations", "name", {"name": name, "address": addr})
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
        name: ensure("groups", "groups", "name", {"name": name, "description": desc})
        for name, desc in [
            ("IT Support", "First-line IT support for employees"),
            ("Platform Engineering", "Owns databases, Kubernetes and internal platform services"),
            ("Facilities", "Office facilities and physical infrastructure"),
        ]
    }

    # Changes can only be assigned to agents who are members of the change's
    # group, so make sure the API user's agent belongs to every demo group.
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
        # (first, last, email, department, job title, location)
        ("Maya", "Cohen", "maya.cohen@demo.robustalabs.dev", "Finance", "Financial Analyst", "Tel Aviv HQ"),
        ("Noa", "Peretz", "noa.peretz@demo.robustalabs.dev", "Finance", "Controller", "Tel Aviv HQ"),
        ("Daniel", "Katz", "daniel.katz@demo.robustalabs.dev", "Sales", "Account Executive", "Tel Aviv HQ"),
        ("Emma", "Johnson", "emma.johnson@demo.robustalabs.dev", "Sales", "Sales Manager", "London Office"),
        ("Sarah", "Mizrahi", "sarah.mizrahi@demo.robustalabs.dev", "Engineering", "Backend Developer", "Tel Aviv HQ"),
        ("James", "Wong", "james.wong@demo.robustalabs.dev", "Engineering", "Site Reliability Engineer", "London Office"),
        ("Priya", "Sharma", "priya.sharma@demo.robustalabs.dev", "Engineering", "Data Engineer", "London Office"),
        ("Liam", "OBrien", "liam.obrien@demo.robustalabs.dev", "IT Operations", "IT Coordinator", "Tel Aviv HQ"),
        # Scenario B laptop fleet
        ("Yossi", "Amar", "yossi.amar@demo.robustalabs.dev", "Sales", "Sales Operations Analyst", "Tel Aviv HQ"),
        ("Grace", "Chen", "grace.chen@demo.robustalabs.dev", "Sales", "Account Manager", "London Office"),
        # Scenario C legal team
        ("Rachel", "Levi", "rachel.levi@demo.robustalabs.dev", "Legal", "Legal Counsel", "Tel Aviv HQ"),
        ("David", "Rosen", "david.rosen@demo.robustalabs.dev", "Legal", "Paralegal", "Tel Aviv HQ"),
        ("Tom", "Baker", "tom.baker@demo.robustalabs.dev", "Legal", "Contracts Manager", "London Office"),
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
# ITAM assets (/api/v2/itam, Device42-based)
#
# Quirks: create/update/delete URLs need a trailing slash (POST without it
# silently acts as a list call), creation requires a 'type' field, does NOT
# upsert by name, and returns {"code": 0, "msg": [...]} instead of the record.
# ---------------------------------------------------------------------------

def seed_assets(ctx: Dict[str, Any]) -> None:
    print("\n== ITAM assets ==")
    try:
        existing = get_all("itam/assets", "assets")
    except RuntimeError as e:
        print(f"  ! ITAM API unavailable, skipping assets: {e}")
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

    specs: List[dict] = [
        # ---- Scenario A: London network edge ----
        {
            "name": "edge-fw-lon-01",
            "type": "Firewall",
            "serial_no": "PA-3410-01144",
            "asset_no": "AST-NET-0101",
            "impact": "High",
            "state": "In Use",
            "notes": (
                "Palo Alto PA-3410 edge firewall for the London office. Terminates all "
                "inbound/outbound traffic for the site, including the GlobalConnect "
                "SSL-VPN tunnels handled by lon-vpn-01 behind it. Firmware managed "
                "through change requests. HA peer planned for Q4."
            ),
            "department_id": dept("IT Operations"),
            "location_id": loc("London Office"),
        },
        {
            "name": "lon-vpn-01",
            "type": "Server",
            "serial_no": "DL360-2024-00871",
            "asset_no": "AST-NET-0102",
            "impact": "High",
            "state": "In Use",
            "notes": (
                "GlobalConnect VPN concentrator for the London office (~90 daily "
                "users). All VPN traffic passes through the upstream edge firewall "
                "edge-fw-lon-01. HPE ProLiant DL360 Gen11. Owned by IT Operations."
            ),
            "department_id": dept("IT Operations"),
            "location_id": loc("London Office"),
        },
        {
            "name": "wifi-ctrl-lon-01",
            "type": "Wireless Controller",
            "serial_no": "ARB-9240-3321",
            "asset_no": "AST-NET-0103",
            "impact": "Medium",
            "state": "In Use",
            "notes": "Aruba 9240 wireless controller for the London office access points.",
            "department_id": dept("IT Operations"),
            "location_id": loc("London Office"),
        },
        # ---- Scenario B: laptop fleet ----
        # ThinkPad X1 Carbon Gen 12 units. Firmware < 1.42 boot-loops under
        # KB5062660; TP-X1C-0290 is on 1.42 and survives (the discriminator).
        {
            "name": "TP-X1C-0251",
            "type": "Laptop",
            "serial_no": "PF-3QMTR7",
            "asset_no": "AST-LAP-0251",
            "impact": "Low",
            "state": "In Use",
            "notes": (
                "Lenovo ThinkPad X1 Carbon Gen 12, 32GB/1TB, Windows 11 23H2. "
                "UEFI firmware 1.38. Intune deployment ring 2 (Finance & Sales). "
                "Assigned to Noa Peretz (Controller)."
            ),
            "user_id": user("noa.peretz@demo.robustalabs.dev"),
            "department_id": dept("Finance"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "TP-X1C-0263",
            "type": "Laptop",
            "serial_no": "PF-8KWLC2",
            "asset_no": "AST-LAP-0263",
            "impact": "Low",
            "state": "In Use",
            "notes": (
                "Lenovo ThinkPad X1 Carbon Gen 12, 16GB/512GB, Windows 11 23H2. "
                "UEFI firmware 1.39. Intune deployment ring 2 (Finance & Sales). "
                "Assigned to Daniel Katz (Account Executive)."
            ),
            "user_id": user("daniel.katz@demo.robustalabs.dev"),
            "department_id": dept("Sales"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "TP-X1C-0277",
            "type": "Laptop",
            "serial_no": "PF-5DNVX8",
            "asset_no": "AST-LAP-0277",
            "impact": "Low",
            "state": "In Use",
            "notes": (
                "Lenovo ThinkPad X1 Carbon Gen 12, 32GB/1TB, Windows 11 23H2. "
                "UEFI firmware 1.38. Intune deployment ring 2 (Finance & Sales). "
                "Assigned to Emma Johnson (Sales Manager)."
            ),
            "user_id": user("emma.johnson@demo.robustalabs.dev"),
            "department_id": dept("Sales"),
            "location_id": loc("London Office"),
        },
        {
            "name": "TP-X1C-0281",
            "type": "Laptop",
            "serial_no": "PF-2ZRHK4",
            "asset_no": "AST-LAP-0281",
            "impact": "Low",
            "state": "In Use",
            "notes": (
                "Lenovo ThinkPad X1 Carbon Gen 12, 16GB/512GB, Windows 11 23H2. "
                "UEFI firmware 1.40. Intune deployment ring 2 (Finance & Sales). "
                "Assigned to Yossi Amar (Sales Operations Analyst)."
            ),
            "user_id": user("yossi.amar@demo.robustalabs.dev"),
            "department_id": dept("Sales"),
            "location_id": loc("Tel Aviv HQ"),
        },
        {
            "name": "TP-X1C-0290",
            "type": "Laptop",
            "serial_no": "PF-9TBQF6",
            "asset_no": "AST-LAP-0290",
            "impact": "Low",
            "state": "In Use",
            "notes": (
                "Lenovo ThinkPad X1 Carbon Gen 12, 32GB/1TB, Windows 11 23H2. "
                "UEFI firmware 1.42 (updated during June hardware refresh). Intune "
                "deployment ring 2 (Finance & Sales). Assigned to Grace Chen "
                "(Account Manager)."
            ),
            "user_id": user("grace.chen@demo.robustalabs.dev"),
            "department_id": dept("Sales"),
            "location_id": loc("London Office"),
        },
        {
            "name": "DELL-LAT-0421",
            "type": "Laptop",
            "serial_no": "3F7GK92",
            "asset_no": "AST-LAP-0421",
            "impact": "Low",
            "state": "In Use",
            "notes": (
                "Dell Latitude 7450, 16GB/512GB, Windows 11 23H2. Intune deployment "
                "ring 2 (Finance & Sales). Assigned to Maya Cohen's team pool, held "
                "by Finance. Unaffected reference device for the July patch cycle."
            ),
            "department_id": dept("Finance"),
            "location_id": loc("Tel Aviv HQ"),
        },
        # ---- Scenario C: the SaaS application ----
        {
            "name": "SignFlow E-Signature",
            "type": "SaaS Application",
            "asset_no": "AST-SAAS-0007",
            "impact": "High",
            "state": "In Use",
            "notes": (
                "SignFlow cloud e-signature platform (signflow.example.com tenant "
                "acme-corp). Business-critical for Legal and Sales: all outbound "
                "contracts and order forms are executed through it (~300 licensed "
                "users). Annual subscription, contract ref SF-2025-4471, renewal date "
                f"{day(SIGNFLOW_EXPIRY)}. Auto-renew was disabled during the Dec 2025 "
                "cost review; renewals are handled manually via change request. "
                "Vendor: SignFlow Inc., account manager Dana Whitfield "
                "(dana.whitfield@signflow.example.com). Renewal owner: IT Operations."
            ),
            "department_id": dept("Legal"),
        },
    ]

    for spec in specs:
        payload = {k: v for k, v in spec.items() if v is not None}
        if find_by(existing, "name", spec["name"]):
            print(f"  = asset exists: {spec['name']}")
            continue
        api("POST", "itam/assets/", payload)
        print(f"  + created asset: {spec['name']} ({spec['type']})")
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Knowledge base
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

    folders = get_all(
        "solutions/folders", "folders", params=f"category_id={category['id']}&"
    )
    folder_specs = [
        ("How-To Guides", "Self-service guides for employees"),
        ("Runbooks", "Operational runbooks for the IT and platform teams"),
        ("Vendor Advisories", "Known issues and advisories for third-party products"),
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
        # ---- Scenario A ----
        (
            "Vendor Advisories",
            "PAN-OS 11.2.3 known issue: GlobalConnect SSL-VPN tunnels fail TLS negotiation",
            f"""<p><b>Product:</b> Palo Alto Networks PAN-OS 11.2.3 (edge firewalls)<br>
<b>Vendor reference:</b> PAN-SA-2026-0071, published {day(NOW - timedelta(days=9))}</p>
<p>PAN-OS 11.2.3 enables <code>strict-cipher-enforcement</code> by default on SSL
decryption profiles. On firewalls that sit in front of a GlobalConnect VPN
concentrator this rejects the cipher suite used by GlobalConnect clients
&le; v6.2, so <b>every SSL-VPN tunnel fails during TLS negotiation</b>. Client-side
symptom: the gateway is reachable but tunnel establishment times out with a
TLS handshake error; the concentrator shows active sessions dropping to zero.</p>
<p><b>Affected:</b> PAN-OS 11.2.3 only. Not affected: 11.2.2 and earlier, 11.2.4+.</p>
<p><b>Remediation options:</b></p>
<ol>
<li>Roll back the firewall to PAN-OS 11.2.2 (see runbook: <i>Edge firewall firmware rollback procedure</i>), or</li>
<li>Upgrade to 11.2.4 which reverts the default, or</li>
<li>Interim workaround: disable <code>strict-cipher-enforcement</code> on the decryption profile covering the VPN concentrator.</li>
</ol>""",
        ),
        (
            "Runbooks",
            "Edge firewall firmware rollback procedure",
            """<p>Applies to the Palo Alto edge firewalls (edge-fw-tlv-01, edge-fw-lon-01).</p>
<ol>
<li>Confirm the previously running PAN-OS image is still on the device: <code>request system software info</code>.</li>
<li>Schedule an emergency change (expedited approval via the IT Support duty manager).</li>
<li>Revert: <code>request system software install version &lt;previous&gt;</code> followed by <code>request restart system</code>. Expect ~8 minutes of downtime for the site.</li>
<li>Verify VPN tunnel establishment from a test client and confirm active session count recovers on the VPN concentrator.</li>
<li>Update the originating change request with the rollback result.</li>
</ol>""",
        ),
        # ---- Scenario B ----
        (
            "Runbooks",
            "Recovering a laptop stuck in Windows Automatic Repair after a failed update",
            """<p>Use this runbook when a Windows laptop boot-loops into Automatic Repair
after a monthly cumulative update.</p>
<ol>
<li>From the Automatic Repair screen: Advanced options &rarr; Troubleshoot &rarr; Uninstall Updates &rarr; <i>Uninstall latest quality update</i>. This restores the previous boot state in ~5 minutes.</li>
<li>If WinRE is not reachable, boot from the IT recovery USB and run <code>dism /image:C:\\ /remove-package</code> for the offending KB.</li>
<li><b>Fleet response:</b> pause the affected Intune update ring immediately (Intune &rarr; Windows updates &rarr; Update rings &rarr; Pause) so later rings do not receive the same update, and raise a problem record for the faulty KB.</li>
<li>Report the device serial and UEFI firmware version in the problem record — update/firmware incompatibilities are usually model- and firmware-specific.</li>
</ol>""",
        ),
        # ---- Scenario C ----
        (
            "Runbooks",
            "SaaS contract register and renewal process",
            f"""<p>Key SaaS subscriptions, their renewal dates and owners. Renewals must be
raised as change requests at least 30 days before expiry (auto-renew is
disabled org-wide since the Dec 2025 cost review).</p>
<table border=\"1\" cellpadding=\"4\">
<tr><th>Service</th><th>Contract ref</th><th>Renewal date</th><th>Annual cost</th><th>Owner</th></tr>
<tr><td>SignFlow E-Signature</td><td>SF-2025-4471</td><td><b>{day(SIGNFLOW_EXPIRY)}</b></td><td>$48,000</td><td>IT Operations</td></tr>
<tr><td>NetSuite ERP</td><td>NS-2024-1180</td><td>{day((NOW + timedelta(days=160)).date())}</td><td>$120,000</td><td>Finance</td></tr>
<tr><td>Grafana Cloud</td><td>GC-2025-0332</td><td>{day((NOW + timedelta(days=95)).date())}</td><td>$36,000</td><td>Platform Engineering</td></tr>
</table>
<p><b>Escalation:</b> if a service is suspended for non-renewal, follow
<i>Emergency SaaS license reinstatement</i> and notify the vendor account manager
listed on the ITAM asset record.</p>""",
        ),
        (
            "Runbooks",
            "Emergency SaaS license reinstatement",
            """<p>When a business-critical SaaS subscription lapses and the vendor suspends
the account:</p>
<ol>
<li>Contact the vendor account manager (listed on the service's ITAM asset record) and request a grace-period reinstatement — most vendors reactivate within 2 hours against a signed PO or written commitment.</li>
<li>Escalate the pending renewal approval to the CAB chair and the budget approver with an <i>emergency change</i> classification.</li>
<li>Post an announcement to affected departments with the expected restoration time.</li>
<li>After service is restored, run a retrospective on why the renewal pipeline stalled (warning ticket ignored? approval bottleneck?).</li>
</ol>""",
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
# Announcements
# ---------------------------------------------------------------------------

def seed_announcements() -> None:
    print("\n== Announcements ==")
    existing = get_all("announcements", "announcements")
    title = "Planned maintenance: London edge firewall firmware upgrade"
    if find_by(existing, "title", title):
        print(f"  = announcement exists: {title}")
        return
    api(
        "POST",
        "announcements",
        {
            "title": title,
            "body_html": (
                f"<p>IT Operations will upgrade the firmware on the London edge "
                f"firewall (edge-fw-lon-01) on {day(YESTERDAY_EVENING.date())} between "
                f"21:00 and 22:00 UTC (change CHN — PAN-OS 11.2.2 &rarr; 11.2.3). "
                f"Expected impact: up to 10 minutes of connectivity loss for the "
                f"London office and remote VPN users. No action needed.</p>"
            ),
            # The API rejects past visible_from values, so go live immediately;
            # the maintenance-window date lives in the body text.
            "visible_from": iso(NOW + timedelta(minutes=2)),
            "visibility": "everyone",
        },
    )
    print(f"  + created announcement: {title}")
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# Changes and releases
# ---------------------------------------------------------------------------

def seed_changes(ctx: Dict[str, Any]) -> None:
    print("\n== Changes ==")
    requesters = ctx["requesters"]
    groups = ctx["groups"]
    liam = requesters["liam.obrien@demo.robustalabs.dev"]
    james = requesters["james.wong@demo.robustalabs.dev"]

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

    # ---- Scenario A culprit: firewall firmware upgrade last night ----
    ensure_change(
        {
            "subject": "Upgrade PAN-OS on London edge firewall edge-fw-lon-01 to 11.2.3",
            "description": (
                "<p>Scheduled firmware maintenance for the London edge firewall. "
                "Upgrade PAN-OS 11.2.2 &rarr; 11.2.3 to pick up the July security "
                "fixes ahead of the quarterly compliance scan.</p>"
            ),
            "requester_id": liam["id"],
            "group_id": groups["IT Support"]["id"],
            "priority": 2,
            "impact": 2,
            "risk": 2,
            "change_type": 2,  # standard
            "status": 5,  # pending review (implemented)
            "planned_start_date": iso(YESTERDAY_EVENING),
            "planned_end_date": iso(YESTERDAY_EVENING + timedelta(hours=1)),
            "planning_fields": {
                "reason_for_change": {
                    "description": (
                        "<p>PAN-OS 11.2.3 contains the July security fix set required "
                        "by the Q3 compliance scan. Vendor lists the release as "
                        "recommended for all edge deployments.</p>"
                    )
                },
                "change_impact": {
                    "description": (
                        "<p>Up to 10 minutes of connectivity loss for the London "
                        "office during the firewall reboot, announced in advance. "
                        "No configuration changes planned — firmware only.</p>"
                    )
                },
                "rollout_plan": {
                    "description": (
                        "<p>1. Snapshot running config and export device state.<br>"
                        "2. Install PAN-OS 11.2.3 image and reboot (~8 min).<br>"
                        "3. Verify HA status, routing adjacencies and outbound "
                        "connectivity from the office LAN.<br>"
                        "4. Close the maintenance window.</p>"
                    )
                },
                "backout_plan": {
                    "description": (
                        "<p>Reinstall the previous PAN-OS 11.2.2 image per the "
                        "'Edge firewall firmware rollback procedure' runbook and "
                        "reboot. Config snapshot taken in step 1.</p>"
                    )
                },
            },
        },
        notes=[
            (
                f"<p>Implementation complete at "
                f"{(YESTERDAY_EVENING + timedelta(minutes=42)).strftime('%Y-%m-%d %H:%M')} UTC. "
                "Firewall rebooted onto 11.2.3, HA healthy, office LAN connectivity "
                "verified, compliance scan agent reports the expected version. "
                "Closing the window. Note: verification covered office egress; "
                "remote-access VPN paths were not exercised (out of hours).</p>"
            )
        ],
    )

    # ---- Scenario A noise: unrelated London change the same week ----
    ensure_change(
        {
            "subject": "Rebalance Wi-Fi channels on London office access points",
            "description": (
                "<p>Adjust channel plan on wifi-ctrl-lon-01 to reduce co-channel "
                "interference reported near the east meeting rooms.</p>"
            ),
            "requester_id": liam["id"],
            "group_id": groups["IT Support"]["id"],
            "priority": 1,
            "impact": 1,
            "risk": 1,
            "change_type": 2,
            "status": 6,  # closed
            "planned_start_date": iso(YESTERDAY_EVENING - timedelta(days=2)),
            "planned_end_date": iso(YESTERDAY_EVENING - timedelta(days=2) + timedelta(hours=1)),
            "planning_fields": {
                "reason_for_change": {
                    "description": "<p>Persistent slow Wi-Fi reports from the east side of the London floor.</p>"
                },
                "change_impact": {
                    "description": "<p>Brief AP reassociations (&lt;30s) during the channel switch.</p>"
                },
                "rollout_plan": {
                    "description": "<p>Apply the new channel plan from the controller, monitor RF health for 24h.</p>"
                },
                "backout_plan": {
                    "description": "<p>Restore the previous channel plan from controller backup.</p>"
                },
            },
        }
    )

    # ---- Scenario B culprit: Windows update ring 2 this morning ----
    ensure_change(
        {
            "subject": "Deploy Windows 11 July cumulative update KB5062660 via Intune - ring 2 (Finance & Sales)",
            "description": (
                "<p>Monthly Windows patch cycle, ring 2 of 3. Deploy the July "
                "cumulative update KB5062660 to all Finance and Sales laptops "
                "via the Intune 'Ring2-Corporate' update ring. Ring 1 (IT pilot, "
                "8 devices) completed last week without issues.</p>"
            ),
            "requester_id": liam["id"],
            "group_id": groups["IT Support"]["id"],
            "priority": 2,
            "impact": 2,
            "risk": 1,
            "change_type": 2,
            "status": 5,  # implemented, pending review
            "planned_start_date": iso(THIS_MORNING - timedelta(hours=4)),
            "planned_end_date": iso(THIS_MORNING),
            "planning_fields": {
                "reason_for_change": {
                    "description": "<p>July Patch Tuesday security updates; KB5062660 fixes two actively exploited CVEs.</p>"
                },
                "change_impact": {
                    "description": "<p>One reboot per device, installed outside active hours (03:00-07:00 local).</p>"
                },
                "rollout_plan": {
                    "description": (
                        "<p>Ring 1 (IT pilot) — done. Ring 2 (Finance &amp; Sales, ~45 "
                        "devices) — this change, overnight window. Ring 3 (all "
                        "remaining laptops, ~200 devices) — scheduled for tomorrow, "
                        "tracked in the 'July Windows patch cycle - ring 3' release.</p>"
                    )
                },
                "backout_plan": {
                    "description": (
                        "<p>Pause the Intune update ring; uninstall KB5062660 via WinRE "
                        "or 'dism /remove-package' per the recovery runbook.</p>"
                    )
                },
            },
        },
        notes=[
            (
                "<p>Intune reports the ring 2 deployment completed overnight: 45/45 "
                "devices installed KB5062660 and rebooted. Compliance dashboard "
                "green as of 06:30 UTC. Will confirm end-user state during the "
                "morning and then approve ring 3.</p>"
            )
        ],
    )

    # ---- Scenario C: renewal change stuck in approval (the process failure) ----
    ensure_change(
        {
            "subject": "Renew SignFlow e-signature subscription for FY2027 (contract SF-2025-4471)",
            "description": (
                f"<p>Annual renewal of the SignFlow e-signature subscription "
                f"(contract SF-2025-4471, expires <b>{day(SIGNFLOW_EXPIRY)}</b>, "
                f"$48,000/yr, ~300 licensed users across Legal and Sales). Renewal "
                f"quote SF-Q-2026-118 received from vendor on "
                f"{day(SIGNFLOW_QUOTE_DATE)}. Auto-renew is disabled org-wide since "
                f"the Dec 2025 cost review, so this renewal requires CAB approval "
                f"before the PO can be issued. <b>Awaiting CAB approval since "
                f"{day(SIGNFLOW_QUOTE_DATE + timedelta(days=2))}.</b></p>"
            ),
            "requester_id": liam["id"],
            "group_id": groups["IT Support"]["id"],
            "priority": 3,
            "impact": 2,
            "risk": 1,
            "change_type": 3,  # major -> requires CAB approval
            "status": 1,  # open: still awaiting approval
            "planned_start_date": iso(NOW + timedelta(days=3)),
            "planned_end_date": iso(NOW + timedelta(days=3, hours=1)),
        },
        notes=[
            (
                f"<p>{day(SIGNFLOW_QUOTE_DATE + timedelta(days=9))}: reminder sent to "
                "the CAB chair and Finance budget approver — no response yet. "
                "Vendor account manager (Dana Whitfield) warned that the account "
                "is suspended automatically if the contract lapses more than 48h.</p>"
            )
        ],
    )

    print("\n== Releases ==")
    releases = get_all("releases", "releases")
    subject = "July Windows patch cycle - ring 3 (all remaining laptops)"
    if not find_by(releases, "subject", subject):
        release = api(
            "POST",
            "releases",
            {
                "subject": subject,
                "description": (
                    "<p>Final ring of the July Windows patch cycle: deploy KB5062660 "
                    "to all remaining corporate laptops (~200 devices, all "
                    "departments) via the Intune 'Ring3-Corporate' update ring. "
                    "Proceeds automatically unless ring 2 review raises a blocker.</p>"
                ),
                "priority": 2,
                "status": 1,
                "release_type": 2,
                "planned_start_date": iso(TOMORROW_MORNING - timedelta(hours=4)),
                "planned_end_date": iso(TOMORROW_MORNING + timedelta(hours=4)),
            },
        )["release"]
        try:
            api("PUT", f"releases/{release['id']}", {"status": 3})  # in progress
        except RuntimeError as exc:
            print(f"    ! could not move release to in-progress: {exc}")
        print(f"  + created release: {subject}")
    else:
        print(f"  = release exists: {subject}")


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

# urgency/impact pairs that make the default priority matrix compute the
# intended priority (low, medium, high, urgent).
PRIORITY_MATRIX = {1: (1, 1), 2: (2, 2), 3: (2, 3), 4: (3, 3)}


def seed_tickets(ctx: Dict[str, Any]) -> None:
    print("\n== Tickets ==")
    groups = ctx["groups"]
    existing = get_all("tickets", "tickets")

    def ensure_ticket(payload: dict, notes: Optional[List[dict]] = None) -> Optional[dict]:
        found = find_by(existing, "subject", payload["subject"])
        if found:
            print(f"  = ticket exists: {payload['subject']}")
            return found
        urgency, impact = PRIORITY_MATRIX[payload.get("priority", 1)]
        payload.setdefault("urgency", urgency)
        payload.setdefault("impact", impact)
        ticket = api("POST", "tickets", payload)["ticket"]
        print(f"  + created ticket #{ticket['id']}: {payload['subject']}")
        for note in notes or []:
            api("POST", f"tickets/{ticket['id']}/notes", note)
            time.sleep(0.3)
        time.sleep(0.3)
        return ticket

    vpn_since = (NOW.replace(hour=6, minute=10, second=0, microsecond=0)).strftime(
        "%H:%M UTC"
    )

    scenario_a = [
        (
            {
                "subject": "Cannot connect to VPN from home - authentication times out",
                "description": (
                    "<p>Trying to start my workday remotely but the GlobalConnect "
                    f"client has been failing since about {vpn_since}. It says "
                    "'connecting' for a minute and then times out. Restarted the "
                    "laptop and my router, same result. Was fine yesterday.</p>"
                ),
                "email": "james.wong@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 2,
                "category": "Network",
                "tags": ["vpn", "london", "remote-access"],
                "group_id": groups["IT Support"]["id"],
            },
            [
                {
                    "body": (
                        "<p>Triage: reproduced from the IT test client. The gateway "
                        "lon-vpn-01 is reachable (ping/HTTPS fine) but tunnel "
                        "negotiation fails with a TLS handshake error. Concentrator "
                        "dashboard shows active sessions dropped from ~85 to 0 at "
                        "06:10 UTC. This is a site-wide outage, not a client issue. "
                        "Checking what changed on the London network path.</p>"
                    ),
                    "private": True,
                },
            ],
        ),
        (
            {
                "subject": "GlobalConnect VPN stuck on 'connecting' for the whole data team",
                "description": (
                    "<p>None of the three of us on the data team can get on the VPN "
                    "this morning. The client hangs on 'connecting' then errors. We "
                    "have a pipeline deployment scheduled for today.</p>"
                ),
                "email": "priya.sharma@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 2,
                "category": "Network",
                "tags": ["vpn", "london"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Can't reach the contracts file share when working remotely",
                "description": (
                    "<p>I need to pull two agreements for a 10:00 signing but the "
                    "legal file share is unreachable from home — the VPN client "
                    "won't connect at all. In-office colleagues can open the share "
                    "fine.</p>"
                ),
                "email": "tom.baker@demo.robustalabs.dev",
                "priority": 2,
                "status": 2,
                "source": 1,
                "category": "Network",
                "tags": ["vpn", "london"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "VPN down - unable to join customer call with demo environment",
                "description": (
                    "<p>Customer demo at 09:30 needs the internal demo environment, "
                    "which is VPN-only. My GlobalConnect client fails with a "
                    "'secure tunnel could not be established' error. Please treat "
                    "as urgent.</p>"
                ),
                "email": "grace.chen@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 3,
                "category": "Network",
                "tags": ["vpn", "london"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
    ]

    scenario_b = [
        (
            {
                "subject": "Laptop stuck on 'Automatic Repair' loop since this morning",
                "description": (
                    "<p>My ThinkPad restarted overnight for updates and now it "
                    "boots into 'Preparing Automatic Repair', restarts, and loops "
                    "forever. I can't work at all. Asset tag AST-LAP-0251.</p>"
                ),
                "email": "noa.peretz@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 3,
                "category": "Hardware",
                "tags": ["boot-failure", "windows-update", "ring2"],
                "group_id": groups["IT Support"]["id"],
            },
            [
                {
                    "body": (
                        "<p>Triage on the device (serial PF-3QMTR7): boot loop began "
                        "after the overnight update window. From WinRE, uninstalling "
                        "the latest quality update (KB5062660) restores normal boot. "
                        "Device is a ThinkPad X1 Carbon Gen 12 on UEFI firmware "
                        "1.38. Checking whether other reports share the same "
                        "hardware profile.</p>"
                    ),
                    "private": True,
                },
            ],
        ),
        (
            {
                "subject": "Blue screen then endless restart cycle on my laptop",
                "description": (
                    "<p>Laptop showed a blue screen when I powered it on at the "
                    "office and now keeps restarting to a repair screen. Nothing "
                    "was spilled/dropped — it was docked overnight as usual.</p>"
                ),
                "email": "daniel.katz@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 2,
                "category": "Hardware",
                "tags": ["boot-failure", "windows-update", "ring2"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "ThinkPad won't boot after overnight update - sales manager",
                "description": (
                    "<p>My laptop installed updates overnight and now cycles "
                    "between the Lenovo logo and a repair screen. I'm presenting "
                    "the pipeline review at 14:00 and everything is on this "
                    "machine.</p>"
                ),
                "email": "emma.johnson@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 2,
                "category": "Hardware",
                "tags": ["boot-failure", "windows-update", "ring2"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Laptop dead after Windows update - boot device not found loop",
                "description": (
                    "<p>Since this morning my laptop loops on startup repair. A "
                    "colleague said their machine did the same. Is something going "
                    "around?</p>"
                ),
                "email": "yossi.amar@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 2,
                "category": "Hardware",
                "tags": ["boot-failure", "windows-update", "ring2"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "Finance team laptop boot failure - month-end close at risk",
                "description": (
                    "<p>Maya's laptop won't get past the repair screen after the "
                    "overnight updates, and month-end close tasks are due today. "
                    "Filing on her behalf since her machine is unusable.</p>"
                ),
                "email": "maya.cohen@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 1,
                "category": "Hardware",
                "tags": ["boot-failure", "windows-update", "ring2"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
    ]

    scenario_c = [
        # The 3-week-old warning ticket (the ignored paper trail)
        (
            {
                "subject": f"SignFlow subscription expires {day(SIGNFLOW_EXPIRY)} - renewal approval needed",
                "description": (
                    f"<p>Heads-up: the SignFlow e-signature contract "
                    f"(SF-2025-4471) expires on <b>{day(SIGNFLOW_EXPIRY)}</b>. "
                    f"Renewal quote SF-Q-2026-118 ($48,000/yr) received on "
                    f"{day(SIGNFLOW_QUOTE_DATE)} and the renewal change request "
                    f"has been raised for CAB approval. Auto-renew is OFF for this "
                    f"vendor. If the approval doesn't land before the expiry date "
                    f"the vendor suspends the account after a 48h grace period — "
                    f"Legal and Sales lose e-signature entirely.</p>"
                ),
                "email": "liam.obrien@demo.robustalabs.dev",
                "priority": 2,
                "status": 2,
                "source": 2,
                "category": "Software",
                "tags": ["signflow", "renewal", "saas"],
                "group_id": groups["IT Support"]["id"],
            },
            [
                {
                    "body": (
                        f"<p>{day(SIGNFLOW_QUOTE_DATE + timedelta(days=9))}: still no "
                        "CAB response on the renewal change. Second reminder sent to "
                        "the budget approver. Vendor confirmed suspension is "
                        "automatic once the grace period ends.</p>"
                    ),
                    "private": True,
                },
            ],
        ),
        (
            {
                "subject": "SignFlow shows 'account suspended' - cannot send contracts for signature",
                "description": (
                    "<p>Since this morning every attempt to send a contract from "
                    "SignFlow fails with 'Your organization's account is "
                    "suspended. Contact your administrator.' Two NDAs and a "
                    "customer MSA are blocked. Web login shows the same banner "
                    "for the whole legal team.</p>"
                ),
                "email": "rachel.levi@demo.robustalabs.dev",
                "priority": 4,
                "status": 2,
                "source": 2,
                "category": "Software",
                "tags": ["signflow", "saas", "legal"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
        (
            {
                "subject": "E-signature requests failing with a license error (HTTP 402)",
                "description": (
                    "<p>The CRM's 'send for signature' button returns an error for "
                    "everyone in the sales workflow. The integration log shows "
                    "<code>402 Payment Required - license_suspended</code> from the "
                    "SignFlow API. Order forms can't go out.</p>"
                ),
                "email": "david.rosen@demo.robustalabs.dev",
                "priority": 3,
                "status": 2,
                "source": 1,
                "category": "Software",
                "tags": ["signflow", "saas"],
                "group_id": groups["IT Support"]["id"],
            },
            None,
        ),
    ]

    for payload, notes in scenario_a + scenario_b + scenario_c:
        ensure_ticket(payload, notes)


def main() -> None:
    print(f"Seeding Freshservice IT demo scenarios at {BASE_URL}")
    ctx = seed_foundation()
    seed_assets(ctx)
    seed_knowledge_base()
    seed_announcements()
    seed_changes(ctx)
    seed_tickets(ctx)
    print(
        "\nDone. Three alert-driven scenarios are ready:\n"
        " A. London VPN outage  -> culprit change: 'Upgrade PAN-OS on London edge firewall edge-fw-lon-01 to 11.2.3'\n"
        " B. Laptop boot loops  -> culprit change: 'Deploy Windows 11 July cumulative update KB5062660 via Intune - ring 2'\n"
        "                          (and ring 3 release scheduled for tomorrow that must be halted)\n"
        " C. SignFlow suspended -> lapsed contract SF-2025-4471; renewal change stuck awaiting CAB approval\n"
        "\nFire the matching alerts with scripts/freshservice_demo/fire_alert.py (see README.md)."
    )


if __name__ == "__main__":
    main()
