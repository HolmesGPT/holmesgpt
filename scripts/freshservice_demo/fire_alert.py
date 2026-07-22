#!/usr/bin/env python3
"""Fire one of the three HolmesGPT demo alerts into Freshservice.

Each alert is the entry point of one demo scenario seeded by
seed_it_scenarios.py: it lands in Freshservice Alert Management, gets
converted into an incident by an alert rule, and that incident triggers the
HolmesGPT investigation workflow.

Preferred mode - post to the Alert Management webhook integration:

    FRESHSERVICE_ALERTS_ENDPOINT="https://<sub>.alerts.freshservice.com/integrations/<INTEGRATION_ID>/alerts" \
    FRESHSERVICE_ALERTS_AUTH="<auth key from the integration page>" \
    python scripts/freshservice_demo/fire_alert.py vpn

The endpoint URL and auth key are shown in the Freshservice UI when you open
the webhook monitoring-tool integration (Admin > IT Operations Management >
Monitoring Tools). FRESHWORK_ALERTS_AUTH is accepted as a fallback env var
for the auth key.

Fallback mode - if you don't have the integration endpoint at hand,
`--as-ticket` creates the alert directly as an incident ticket through the
regular API (FRESHSERVICE_URL/FRESHSERVICE_API_KEY or FRESHWORK_URL/
FRESHWORK_API_KEY):

    python scripts/freshservice_demo/fire_alert.py laptops --as-ticket

Scenarios: vpn | laptops | signflow
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

ALERTS = {
    "vpn": {
        "subject": "lon-vpn-01: all GlobalConnect SSL-VPN tunnels down (active sessions 0)",
        "description": (
            f"GlobalConnect tunnel establishment is failing for all users since "
            f"{TODAY} 06:10 UTC. Gateway lon-vpn-01 is reachable but every tunnel "
            f"negotiation aborts with a TLS handshake error. Active session count "
            f"dropped from ~85 to 0 and has stayed at 0 for 15 minutes. "
            f"Source check: vpn_session_monitor on lon-vpn-01."
        ),
        "severity": "Critical",
        "resource": "lon-vpn-01",
        "node": "lon-vpn-01",
        "metric_name": "vpn_active_sessions",
        "metric_value": "0",
        "tags": ["vpn", "london", "remote-access"],
    },
    "laptops": {
        "subject": "Intune Ring2-Corporate: 6 devices unreachable with boot failure state since 07:00 UTC",
        "description": (
            f"Endpoint health monitoring: 6 corporate laptops in the Intune "
            f"'Ring2-Corporate' device group have not checked in since the "
            f"overnight update window and their last-known state is "
            f"'startup repair / boot failure' ({TODAY} 07:00 UTC). Affected user "
            f"count is rising as employees start work. "
            f"Source check: intune_device_health."
        ),
        "severity": "Critical",
        "resource": "intune-ring2-corporate",
        "node": "intune-connector",
        "metric_name": "devices_boot_failure",
        "metric_value": "6",
        "tags": ["endpoint", "windows-update", "ring2"],
    },
    "signflow": {
        "subject": "SignFlow e-signature: synthetic checks failing with license_suspended (HTTP 402)",
        "description": (
            f"Synthetic transaction monitoring for the SignFlow tenant "
            f"acme-corp: the login-and-send-document check has failed every run "
            f"since {TODAY} 05:30 UTC. The SignFlow API returns HTTP 402 "
            f"'license_suspended' and the web UI shows an 'account suspended' "
            f"banner. All 300 licensed users are affected. "
            f"Source check: saas_synthetic_signflow."
        ),
        "severity": "Critical",
        "resource": "signflow-saas",
        "node": "synthetic-probe-eu1",
        "metric_name": "synthetic_login_check",
        "metric_value": "failed",
        "tags": ["saas", "signflow", "license"],
    },
}


def fire_webhook(alert: dict) -> None:
    endpoint = os.environ.get("FRESHSERVICE_ALERTS_ENDPOINT")
    auth = os.environ.get("FRESHSERVICE_ALERTS_AUTH") or os.environ.get(
        "FRESHWORK_ALERTS_AUTH"
    )
    if not endpoint or not auth:
        print(
            "Set FRESHSERVICE_ALERTS_ENDPOINT (the webhook integration URL from "
            "Admin > IT Operations Management > Monitoring Tools) and "
            "FRESHSERVICE_ALERTS_AUTH (its auth key), or use --as-ticket."
        )
        sys.exit(1)
    payload = {
        "name": alert["subject"],
        "message": alert["subject"],
        "description": alert["description"],
        "severity": alert["severity"],
        "status": "Open",
        "resource": alert["resource"],
        "node": alert["node"],
        "metric_name": alert["metric_name"],
        "metric_value": alert["metric_value"],
        "alert_tags": ",".join(alert["tags"]),
    }
    resp = requests.post(
        endpoint,
        json=payload,
        headers={"Authorization": auth, "Content-Type": "application/json"},
        timeout=30,
    )
    print(f"POST {endpoint} -> {resp.status_code}")
    print(resp.text[:500])
    if resp.status_code not in (200, 201, 202):
        sys.exit(1)


def fire_ticket(alert: dict) -> None:
    base = (
        os.environ.get("FRESHSERVICE_URL") or os.environ.get("FRESHWORK_URL") or ""
    ).rstrip("/")
    key = os.environ.get("FRESHSERVICE_API_KEY") or os.environ.get(
        "FRESHWORK_API_KEY"
    )
    if not base or not key:
        print("Set FRESHSERVICE_URL and FRESHSERVICE_API_KEY (or FRESHWORK_*)")
        sys.exit(1)
    description = (
        f"<p>{alert['description']}</p>"
        f"<p>Resource: <b>{alert['resource']}</b> | Node: {alert['node']} | "
        f"Metric: {alert['metric_name']} = {alert['metric_value']} | "
        f"Severity: {alert['severity']}</p>"
    )
    resp = requests.post(
        f"{base}/api/v2/tickets",
        json={
            "subject": alert["subject"],
            "description": description,
            "email": "alerts@demo.robustalabs.dev",
            "priority": 4,
            "urgency": 3,
            "impact": 3,
            "status": 2,
            "tags": alert["tags"] + ["monitoring-alert"],
        },
        auth=(key, "X"),
        timeout=30,
    )
    print(f"POST {base}/api/v2/tickets -> {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(resp.text[:500])
        sys.exit(1)
    ticket = resp.json()["ticket"]
    print(f"Created incident #{ticket['id']}: {ticket['subject']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=sorted(ALERTS.keys()))
    parser.add_argument(
        "--as-ticket",
        action="store_true",
        help="create the alert as an incident ticket via the regular API "
        "instead of posting to the Alert Management webhook",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the payload and exit"
    )
    args = parser.parse_args()

    alert = ALERTS[args.scenario]
    if args.dry_run:
        print(json.dumps(alert, indent=2))
        return
    if args.as_ticket:
        fire_ticket(alert)
    else:
        fire_webhook(alert)


if __name__ == "__main__":
    main()
