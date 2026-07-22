# HolmesGPT + Freshservice IT Demo Scenarios

Three alert-driven demo scenarios that showcase HolmesGPT investigating IT
incidents using Freshservice data (tickets, changes, releases, ITAM assets,
knowledge base, announcements) and opening a ticket with its analysis.

Every scenario follows the same arc:

1. A **monitoring alert** fires into Freshservice (Alert Management webhook or
   an incident ticket).
2. The alert **triggers a HolmesGPT workflow**.
3. Holmes investigates using the **Freshservice toolset** — correlating the
   alert with user tickets, recent changes, asset records, KB articles and
   announcements.
4. Holmes **opens a ticket** (or problem) containing the root-cause analysis
   and a recommendation.

## Setup

```bash
# Seed all demo data (idempotent - safe to re-run any time)
FRESHSERVICE_URL=https://<subdomain>.freshservice.com \
FRESHSERVICE_API_KEY=xxx \
python scripts/freshservice_demo/seed_it_scenarios.py
```

`FRESHWORK_URL` / `FRESHWORK_API_KEY` are accepted as fallback env var names.

The seeder creates (or reuses) the shared foundation — departments (including
Legal), the London/Tel Aviv locations, agent groups, 13 requesters — plus the
per-scenario records listed below. Everything is looked up by
name/email/subject before creation, so re-running never duplicates data.

> **Plan limitation:** this Freshservice plan does not include the classic
> CMDB, Contracts, Purchase Orders, Products or Vendors modules (the API
> returns 403 `require_feature`). Assets therefore live in the newer ITAM
> module (`/api/v2/itam/assets`), and contract/vendor facts are carried on
> the ITAM asset record and a "SaaS contract register" KB article instead.

---

## The three alerts that trigger the demos

Each demo starts by firing one alert. Two ways to do it:

### Option 1 — Alert Management webhook (preferred)

Freshservice Alert Management converts incoming alerts into incidents via
alert rules, which is the trigger for the Holmes workflow.

1. In Freshservice: **Admin → IT Operations Management → Monitoring Tools**,
   open (or create) the **Webhook** integration and copy its **endpoint URL**
   and **auth key**. The endpoint looks like
   `https://<subdomain>.alerts.freshservice.com/integrations/<INTEGRATION_ID>/alerts`
   and the auth key is a JWT passed in the `Authorization` header.
   (The integration ID is only visible in the UI — it cannot be discovered
   via the API.)
2. Make sure an **alert rule** exists that creates an incident from incoming
   alerts (Alert Management → Alert Rules), and that your Holmes workflow is
   subscribed to those incidents.
3. Fire the scenario's alert:

```bash
export FRESHSERVICE_ALERTS_ENDPOINT="https://<subdomain>.alerts.freshservice.com/integrations/<INTEGRATION_ID>/alerts"
export FRESHSERVICE_ALERTS_AUTH="<auth key>"      # FRESHWORK_ALERTS_AUTH also works

python scripts/freshservice_demo/fire_alert.py vpn        # Scenario A
python scripts/freshservice_demo/fire_alert.py laptops    # Scenario B
python scripts/freshservice_demo/fire_alert.py signflow   # Scenario C
```

If your webhook integration uses custom field mapping, map the payload keys
`resource`, `node`, `metric_name`, `metric_value`, `severity`, `message`,
`description` and `alert_tags` (use `--dry-run` to see the exact payload).

### Option 2 — direct incident (no Alert Management needed)

Creates the same alert as a monitoring incident ticket through the regular
API — useful if the webhook endpoint isn't at hand:

```bash
python scripts/freshservice_demo/fire_alert.py vpn --as-ticket
```

### Alert definitions

| Scenario | Alert subject | Resource | Metric | Severity |
|---|---|---|---|---|
| A | `lon-vpn-01: all GlobalConnect SSL-VPN tunnels down (active sessions 0)` | `lon-vpn-01` | `vpn_active_sessions = 0` | Critical |
| B | `Intune Ring2-Corporate: 6 devices unreachable with boot failure state since 07:00 UTC` | `intune-ring2-corporate` | `devices_boot_failure = 6` | Critical |
| C | `SignFlow e-signature: synthetic checks failing with license_suspended (HTTP 402)` | `signflow-saas` | `synthetic_login_check = failed` | Critical |

If you create the alerts manually in a monitoring tool instead, use the
subjects/descriptions from `fire_alert.py --dry-run` verbatim — the resource
names (`lon-vpn-01`, ring-2 device group, SignFlow tenant) are the breadcrumbs
Holmes uses to pivot into the Freshservice data.

---

## Scenario A — "The Expired Certificate" (VPN outage; the change is a red herring)

**Root cause:** the TLS certificate serving the London GlobalConnect VPN
(`vpn-lon.acme-corp.com`) expired at 23:59 UTC last night. Every tunnel fails
TLS negotiation at the certificate-validation stage from the first morning
connection attempts. A PAN-OS firmware upgrade ran on the upstream firewall
the **same evening** — a deliberate red herring that the evidence exonerates.

**Seeded records:**

- ITAM assets: `lon-vpn-01` (VPN concentrator — the alert's resource; notes
  point at both the upstream firewall and the certificate asset),
  **`vpn-lon.acme-corp.com TLS certificate`** (type "SSL Certificate",
  AST-CRT-0001) whose notes carry the validity window ending **yesterday** —
  this is where Holmes verifies the expiry — plus a note that this hostname
  is not enrolled in the automated expiry watcher (why nobody was warned),
  `edge-fw-lon-01` (Firewall), `wifi-ctrl-lon-01` (decoy).
- Change **CHN-6** *"Upgrade PAN-OS on London edge firewall edge-fw-lon-01 to
  11.2.3"* — implemented last night 21:00 UTC. Red herring: its
  implementation note records a **successful GlobalConnect test tunnel at
  21:45 UTC**, after the upgrade — the outage started later, when the cert
  expired at 23:59.
- Decoy change CHN-7 (London Wi-Fi channel rebalance, closed, 3 days ago).
- Announcement for the firewall maintenance window.
- 4 user tickets (#32–#35) from London requesters; #32 carries two triage
  notes: sessions 85 → 0 at 06:10 UTC, and the client error *"the server
  certificate is not valid (expired or not yet valid)"*.
- KB: vendor advisory *"PAN-OS 11.2.0/11.2.1 known issue..."* — explicitly
  **not applicable to 11.2.3** and pointing certificate-validation failures
  at the cert instead — plus *"Renewing the GlobalConnect VPN TLS
  certificate"* and *"Edge firewall firmware rollback procedure"*.

**Expected Holmes analysis:** VPN alert on `lon-vpn-01` → cluster of London
VPN tickets → the obvious suspect is last night's firewall change CHN-6 →
but the change note shows VPN worked at 21:45 post-upgrade, and the vendor
advisory says 11.2.3 is not affected → triage note says the failure is
certificate validation → the certificate ITAM asset proves
`vpn-lon.acme-corp.com` expired at 23:59 UTC yesterday → **root cause:
expired certificate; recommend emergency reissue per the renewal runbook**,
and flag the process gap (hostname missing from the expiry watcher). Holmes
opens a major-incident ticket exonerating CHN-6 and citing the certificate
asset.

## Scenario B — "Patch Tuesday Strikes Back" (asset-pattern detection)

**Root cause:** Windows update KB5062660 (deployed overnight to Intune ring 2)
boot-loops Lenovo ThinkPad X1 Carbon Gen 12 devices on UEFI firmware < 1.42.
**The punchline:** ring 3 — ~200 more laptops — is scheduled for **tomorrow**
and must be halted.

**Seeded records:**

- ITAM laptop assets: 5 ThinkPad X1 Carbon Gen 12 on firmware 1.38–1.40
  (broken), 1 on firmware 1.42 (`TP-X1C-0290`, survives — the discriminator),
  plus a Dell Latitude and MacBooks (unaffected).
  Each asset names its assigned user and "Intune deployment ring 2".
- Change **CHN-8** *"Deploy Windows 11 July cumulative update KB5062660 via
  Intune - ring 2 (Finance & Sales)"* — implemented this morning, with a note
  that Intune reported success and ring 3 would be approved.
- Release *"July Windows patch cycle - ring 3 (all remaining laptops)"* —
  planned for tomorrow, in progress.
- 5 boot-loop tickets (#36–#40); #36 carries a triage note (WinRE uninstall of
  KB5062660 fixes it; device is a Gen 12 on firmware 1.38).
- KB: *"Recovering a laptop stuck in Windows Automatic Repair after a failed
  update"* (includes "pause the ring" fleet guidance).

**Expected Holmes analysis:** endpoint alert → boot-failure tickets → look up
each reporter's laptop asset → **every failing device is a ThinkPad X1 Carbon
Gen 12 on firmware < 1.42; the Gen 12 on 1.42 is fine** → correlate with
CHN-8 → find the ring-3 release scheduled tomorrow → open a
problem/incident: KB5062660 × Gen 12 firmware < 1.42, **halt the ring-3
release before ~200 more devices brick**, recover devices per the runbook.

## Scenario C — "The Silent Expiry" (process failure, no technical change)

**Root cause:** the SignFlow e-signature SaaS suspended the account because
contract SF-2025-4471 expired two days ago. The renewal was quoted three weeks
ago, but the renewal change request is still stuck awaiting CAB approval and
the warning ticket went unanswered. Nothing in the infrastructure changed.

**Seeded records:**

- ITAM asset *"SignFlow E-Signature"* (type "SaaS Application") — carries the
  contract ref, renewal date, cost, vendor account manager and the note that
  auto-renew was disabled in the Dec 2025 cost review.
- Change **CHN-9** *"Renew SignFlow e-signature subscription for FY2027"* —
  still **open** (awaiting CAB approval since 3 weeks ago), with a reminder
  note warning about automatic suspension.
- Warning ticket #41 (filed 3 weeks ago in narrative) + 2 outage tickets
  (#42–#43) from Legal with the `402 license_suspended` evidence.
- KB: *"SaaS contract register and renewal process"* (lists SignFlow's expiry
  date) + *"Emergency SaaS license reinstatement"*.

**Expected Holmes analysis:** synthetic-check alert (HTTP 402
license_suspended) → outage tickets → **rule out infrastructure changes** (no
correlated change) → SaaS asset record reveals the contract expiry date has
passed → renewal change CHN-9 stuck in approval + ignored warning ticket #41
→ open a ticket with the full paper trail: *"service down because the
renewal approval sat for 21 days"*, recommend the emergency reinstatement
runbook, contact the vendor account manager, and expedite CHN-9.

---

## Triggering Holmes

Configure the Robusta workflow so that alert-created incidents launch an
investigation with this prompt:

```text
A Freshservice monitoring alert created this incident. You are the on-call
investigator: find the root cause using Freshservice data, open an RCA ticket,
and report back.

INVESTIGATE
1. Read this incident's subject, description and fields. The alert's resource,
   metric and start time identify the affected system - use them as your
   pivot into the rest of the data.
2. Gather evidence from Freshservice (read tools):
   - Open tickets with related symptoms (same resource, location, tags or
     error text). Read their conversations - agent triage notes often contain
     key measurements.
   - Recently implemented changes and in-progress releases. Compare each
     change's planned window and rollout plan against the alert start time and
     the affected resource. Read change notes.
   - ITAM asset records for the affected resource AND for the devices/users on
     the related tickets. Asset notes contain dependencies (e.g. upstream
     network devices), model and firmware versions, ownership, and
     contract/renewal details. Look for patterns across assets (same model,
     same firmware, same ring, same site).
   - Knowledge base articles and vendor advisories matching the product and
     symptoms.
   - Announcements about recent maintenance windows.
3. Conclude only from evidence and cite record IDs (ticket #, CHN-, asset
   names, KB titles). If no change correlates with the alert, do not force
   one - consider process failures instead: expired contracts or renewal
   dates on asset records, change requests stuck in approval, warning tickets
   that went unanswered.

REPORT
4. Create ONE new Freshservice ticket (freshservice_create_object,
   object_type "tickets") with:
   - subject: "[Holmes RCA] <one-line root cause>"
   - description containing: root cause; the evidence chain with record IDs;
     scope (affected users, sites, departments); recommended remediation
     citing the relevant runbook/KB article; and any URGENT follow-up (e.g. a
     scheduled release that must be halted, an approval that must be
     expedited, a vendor contact to call).
   - email "holmes@demo.robustalabs.dev", priority matching the impact.
5. Update the triggering incident: add a private note
   (freshservice_create_related_object, sub_resource "notes") that summarizes
   the root cause in 2-3 sentences and references the RCA ticket ID from
   step 4 so the two records are linked.
6. Send a Slack message to the incident channel: alert subject, root cause in
   one paragraph, the RCA ticket ID, and the single most urgent recommended
   action. Use plain text suitable for Slack (no HTML).

Do not modify or delete any other existing records.
```

**Updating the alert:** Alert Management alerts have no public write API, so
"updating the alert" is done by noting the incident the alert rule created
(step 5) — that's the record the alert timeline links to in the UI.

**Slack:** step 6 assumes a Slack tool is available to Holmes (Slack
toolset/MCP or the platform's Slack integration). If the workflow itself has
a Slack notification action, remove step 6 and let the workflow route the
summary instead — one or the other, to avoid double posts.

**Toolset config** — writes must be enabled and, for an unattended workflow,
auto-approved:

```yaml
toolsets:
  freshservice:
    enabled: true
    config:
      api_url: "https://<subdomain>.freshservice.com"
      api_key: "{{ env.FRESHSERVICE_API_KEY }}"
      enable_write_tools: true
      require_approval_for_writes: false   # unattended workflow: no human in the loop
```

## Resetting between demos

Re-running the seeder never duplicates records. To reset a scenario after a
demo run, delete only what the demo added: the alert-created incident and the
ticket/problem Holmes opened (Freshservice → Tickets → Delete). The seeded
records can stay — they are the "steady state" of the fictional org.
