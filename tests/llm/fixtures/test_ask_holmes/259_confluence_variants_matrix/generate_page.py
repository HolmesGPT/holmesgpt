#!/usr/bin/env python3
"""Build the Confluence storage-format HTML body used by the 259 matrix eval.

The page is a fake "Internal Service Audit Catalogue" with ~25 service
sections of distractor content, plus a single audit-summary table that
contains the verification code the eval prompt asks Holmes to find.

Why the structure is the way it is:

* The page is large enough (~10K tokens) that the three Confluence
  variants give meaningfully different token footprints, but small enough
  to fit comfortably below the per-tool truncation limit so all variants
  can in principle pass.
* The verification code lives inside ``<table class="audit-summary">`` so
  the ``confluence_html_filter`` variant has a clean CSS selector to
  narrow the body down to.
* Distractor content uses plausible service-catalogue language — it does
  not hint at the answer or the table structure.

Output: prints a JSON-encoded string (the page body) to stdout, ready to
be embedded into a Confluence content-creation payload.
"""

import json

VERIFICATION_CODE = "AUDIT-2025-HOLMES-q9P3vL7m"

SERVICES = [
    ("checkout-api", "Order checkout & payment routing", "go", "us-east-1"),
    ("inventory-svc", "Stock keeping & reservation", "java", "eu-west-1"),
    ("user-profile", "User identity & preferences", "python", "us-west-2"),
    ("notification-bus", "Outbound emails, SMS, push", "node", "us-east-1"),
    ("shipping-quote", "Carrier rate aggregation", "go", "eu-west-1"),
    ("loyalty-engine", "Points & tiered rewards", "kotlin", "us-east-2"),
    ("search-frontdoor", "Catalogue search edge", "rust", "ap-south-1"),
    ("pricing-svc", "Discount & promo evaluation", "python", "us-east-1"),
    ("cart-state", "Cart persistence & merge", "java", "us-east-1"),
    ("returns-portal", "Return authorization flow", "ruby", "us-west-2"),
    ("fraud-scorer", "Realtime fraud scoring", "python", "us-east-2"),
    ("warehouse-sync", "WMS adapter", "java", "eu-west-1"),
    ("auth-broker", "OIDC token broker", "go", "us-east-1"),
    ("review-api", "Product reviews CRUD", "node", "ap-south-1"),
    ("recommendation", "Recsys serving layer", "python", "us-west-2"),
    ("feature-flags", "Flag evaluation cache", "go", "us-east-1"),
    ("billing-export", "Invoice generation jobs", "java", "eu-west-1"),
    ("media-pipeline", "Image & video transcode", "python", "us-east-2"),
    ("legal-archive", "Document retention store", "java", "eu-central-1"),
    ("partner-webhooks", "3p webhook fan-out", "node", "us-east-1"),
    ("analytics-ingest", "Event collection edge", "go", "us-east-1"),
    ("session-store", "Session token cache", "rust", "us-east-1"),
    ("price-history", "Price journal storage", "python", "us-west-2"),
    ("address-norm", "Address normalization", "java", "us-east-1"),
    ("gift-cards", "Stored value ledger", "kotlin", "us-east-2"),
]

# Roughly 4 paragraphs per service to bulk the page out without making the
# distractor look too obviously like filler. Phrases are deliberately
# generic SRE language; nothing here points at where the audit code lives.
PARAGRAPH_TEMPLATES = [
    "The {name} service is owned by the {team} team and follows the platform's "
    "standard observability conventions: structured logs go to the central "
    "Loki tenant, RED metrics are emitted via the OpenTelemetry collector, and "
    "tracing is enabled at the ingress and database call sites. Runbooks live "
    "in the team's section of this space.",
    "Capacity is planned quarterly. {name} currently runs with a baseline of "
    "8 pods scaled by HPA on CPU at 70% utilization, with a hard ceiling of "
    "40 pods to protect downstream dependencies during traffic spikes. The "
    "scaling policy was last reviewed in the most recent platform sync.",
    "Deployment uses the standard rolling-update strategy with a 25% surge "
    "and 0% unavailable window. Canaries are gated by the platform's "
    "progressive-delivery controller; rollbacks are automatic when the "
    "5-minute error-rate SLO budget is breached.",
    "{name} consumes secrets from the shared vault namespace and rotates "
    "service-account credentials every 90 days. Database credentials are "
    "issued dynamically by the secrets manager; static credentials are "
    "explicitly disallowed by the platform admission controller.",
]

TEAMS = ["payments", "fulfilment", "identity", "growth", "platform", "data", "trust-and-safety"]


def render_service_section(idx: int, svc: tuple) -> str:
    name, desc, lang, region = svc
    team = TEAMS[idx % len(TEAMS)]
    paragraphs = "".join(
        f"<p>{tpl.format(name=name, team=team)}</p>" for tpl in PARAGRAPH_TEMPLATES
    )
    deps_table = (
        "<table class='deps-table'>"
        "<thead><tr><th>Dependency</th><th>Type</th><th>SLO</th></tr></thead>"
        "<tbody>"
        f"<tr><td>postgres-{name}</td><td>datastore</td><td>99.95%</td></tr>"
        f"<tr><td>kafka-{region}</td><td>broker</td><td>99.9%</td></tr>"
        f"<tr><td>redis-{region}</td><td>cache</td><td>99.95%</td></tr>"
        "</tbody></table>"
    )
    return (
        f"<h2 id='svc-{name}'>{name}</h2>"
        f"<p><strong>Purpose:</strong> {desc}. <strong>Language:</strong> {lang}. "
        f"<strong>Primary region:</strong> {region}. <strong>Owner:</strong> {team}.</p>"
        f"{paragraphs}"
        f"{deps_table}"
    )


def render_audit_summary() -> str:
    # The verification code is buried in the last row of the audit-summary
    # table. The class attribute is what the html_filter variant can latch
    # onto to bring just this table into context.
    return (
        "<h2 id='audit-summary'>Quarterly Audit Summary</h2>"
        "<p>The following table records the audit reference codes issued by "
        "the platform compliance team. Each code is tied to a specific quarter "
        "and is the canonical identifier used in audit correspondence.</p>"
        "<table class='audit-summary'>"
        "<thead><tr><th>Quarter</th><th>Auditor</th><th>Scope</th><th>Audit Code</th></tr></thead>"
        "<tbody>"
        "<tr><td>2024-Q1</td><td>Internal</td><td>Identity & Access</td><td>AUDIT-2024-Q1-IAM-redacted</td></tr>"
        "<tr><td>2024-Q2</td><td>External</td><td>Payments PCI scope</td><td>AUDIT-2024-Q2-PCI-redacted</td></tr>"
        "<tr><td>2024-Q3</td><td>Internal</td><td>Data retention</td><td>AUDIT-2024-Q3-RET-redacted</td></tr>"
        "<tr><td>2024-Q4</td><td>External</td><td>SOC 2 Type II</td><td>AUDIT-2024-Q4-SOC-redacted</td></tr>"
        f"<tr><td>2025-Q1</td><td>External</td><td>Full platform audit</td><td>{VERIFICATION_CODE}</td></tr>"
        "</tbody></table>"
    )


def build_body() -> str:
    parts = [
        "<h1>Internal Service Audit Catalogue</h1>",
        "<p>This page is the canonical reference for service ownership, "
        "capacity, deployment, and audit metadata across the platform. It is "
        "regenerated by the platform observability pipeline at the start of "
        "every quarter and reviewed by the compliance team.</p>",
    ]
    for idx, svc in enumerate(SERVICES):
        parts.append(render_service_section(idx, svc))
    parts.append(render_audit_summary())
    return "".join(parts)


if __name__ == "__main__":
    print(json.dumps(build_body()))
