"""Generate a very large Confluence page (~150K+ characters) with a verification
code buried near the end.  The page simulates a detailed incident post-mortem
catalogue so the content looks realistic.  The verification code is embedded in
a single row of a summary table that appears after tens of thousands of words of
prose — far beyond the per-tool token limit (~25K tokens / ~19K for a 128K
context window).
"""

import hashlib
import json
import random
import sys
import textwrap
import time

VERIFICATION_CODE = "HOLMES-EVAL-vK7w3nR9pL"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVICES = [
    "checkout-api", "user-service", "inventory-db", "payment-gateway",
    "notification-hub", "catalog-sync", "analytics-pipeline", "search-indexer",
    "media-processor", "auth-proxy", "rate-limiter", "config-server",
    "session-store", "cache-warmer", "logging-collector", "metrics-exporter",
    "event-bus", "scheduler-daemon", "batch-processor", "data-migrator",
]

_ENVS = ["dev", "staging", "production", "dr-west", "dr-east"]

_ROOT_CAUSES = [
    "memory leak in connection pool", "certificate expiry", "DNS resolution timeout",
    "disk I/O saturation", "kernel OOM killer", "CPU throttling due to cgroup limits",
    "network partition between AZs", "corrupted WAL segment", "lock contention on shared mutex",
    "thread pool exhaustion", "GC pause exceeding health-check timeout",
    "misconfigured autoscaler floor", "expired service-account token",
    "race condition in cache invalidation", "silent data corruption in replication stream",
    "upstream rate-limit enforcement change", "TLS handshake failure after library upgrade",
    "pod eviction due to ephemeral storage pressure", "zombie process accumulation",
    "incorrect iptables rule after CNI upgrade",
]


def _paragraph(seed: int, min_sentences: int = 8, max_sentences: int = 15) -> str:
    """Generate a deterministic realistic-looking paragraph."""
    rng = random.Random(seed)
    templates = [
        "The {service} experienced degraded performance in {env} for approximately {mins} minutes starting at {time} UTC.",
        "Investigation revealed that the root cause was {cause} affecting the {service} deployment.",
        "Mitigation involved rolling back the {service} to the previous stable release and scaling replicas from {n1} to {n2}.",
        "The on-call engineer was paged at {time} UTC and acknowledged within {ack} minutes.",
        "Customer impact was limited to {pct}% of requests returning 5xx errors during the incident window.",
        "Post-incident review identified {n1} action items, of which {n2} have been completed as of the last review cycle.",
        "The {service} team implemented additional circuit-breaker logic to prevent cascading failures in {env}.",
        "Monitoring gaps were addressed by adding a new alert on {metric} with a {mins}-minute evaluation window.",
        "A detailed timeline was shared in the #incidents Slack channel and archived in the incident management system.",
        "Capacity planning for {env} was revised to include a {pct}% headroom buffer following this incident.",
        "The deployment pipeline for {service} was updated to include a canary phase with automated rollback on error-rate spikes.",
        "Database connection pool settings were tuned from {n1} to {n2} connections per replica after profiling under load.",
        "The team conducted a blameless retrospective and documented lessons learned in the engineering wiki.",
        "Dependency analysis showed that {n1} downstream services were affected, with {n2} requiring manual intervention.",
        "SLO compliance for the quarter dropped to {pct}% due to this incident, triggering an error budget policy review.",
    ]
    sentences = []
    for _ in range(rng.randint(min_sentences, max_sentences)):
        tpl = rng.choice(templates)
        s = tpl.format(
            service=rng.choice(_SERVICES),
            env=rng.choice(_ENVS),
            mins=rng.randint(3, 120),
            time=f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}",
            cause=rng.choice(_ROOT_CAUSES),
            n1=rng.randint(1, 20),
            n2=rng.randint(2, 50),
            ack=rng.randint(1, 15),
            pct=round(rng.uniform(0.1, 45.0), 1),
            metric=rng.choice(["p99_latency", "error_rate", "cpu_utilization",
                               "memory_pressure", "disk_iops", "connection_count"]),
        )
        sentences.append(s)
    return " ".join(sentences)


def _incident_section(incident_id: int, seed: int) -> str:
    """Generate a single incident section (~800-1200 words)."""
    rng = random.Random(seed)
    svc = rng.choice(_SERVICES)
    env = rng.choice(_ENVS)
    cause = rng.choice(_ROOT_CAUSES)
    year = rng.choice([2023, 2024, 2025])
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)

    parts = [
        f"<h3>INC-{incident_id:04d}: {svc} outage in {env}</h3>",
        f"<p><strong>Date:</strong> {year}-{month:02d}-{day:02d} | "
        f"<strong>Severity:</strong> P{rng.randint(1,4)} | "
        f"<strong>Duration:</strong> {rng.randint(5, 180)} minutes | "
        f"<strong>Root cause:</strong> {cause}</p>",
    ]
    for sub in ["Summary", "Timeline", "Root Cause Analysis", "Remediation",
                "Lessons Learned", "Action Items"]:
        parts.append(f"<h4>{sub}</h4>")
        # 2-3 paragraphs per subsection
        for j in range(rng.randint(2, 3)):
            parts.append(f"<p>{_paragraph(seed * 100 + hash(sub) + j)}</p>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main page generation
# ---------------------------------------------------------------------------

def generate_page_body() -> str:
    parts = []

    # --- Introduction (~500 words) ---
    parts.append("<h1>Platform Incident Post-Mortem Catalogue 2023-2025</h1>")
    parts.append(textwrap.dedent("""\
    <h2>Purpose</h2>
    <p>This document is the authoritative record of all platform incidents classified
    as P1 through P4 across all environments.  It is maintained by the Site Reliability
    Engineering team and reviewed quarterly by the Architecture Review Board.  Every
    incident includes a timeline, root-cause analysis, remediation steps, and follow-up
    action items.  Teams are expected to reference this catalogue when writing new
    runbooks or proposing architectural changes.</p>
    """))

    parts.append(textwrap.dedent("""\
    <h2>Incident Classification</h2>
    <p>Incidents are classified by customer impact and blast radius.  P1 incidents
    affect more than 50% of production traffic or involve data loss.  P2 incidents
    affect a single service with measurable customer impact.  P3 incidents cause
    degraded performance without breaching SLO budgets.  P4 incidents are near-misses
    or internal-only issues caught before reaching customers.</p>
    """))

    # --- Generate ~80 incident sections ---
    # Each section is ~800-1200 words.  80 sections ≈ 70-96K words ≈ 280-384K chars
    # This ensures the page far exceeds the 25K token tool limit.
    num_incidents = 80
    for i in range(num_incidents):
        incident_id = 1001 + i
        parts.append(_incident_section(incident_id, seed=incident_id))

    # --- The needle: a summary table buried at the very end ---
    parts.append("<h2>Quarterly Reliability Summary</h2>")
    parts.append(textwrap.dedent("""\
    <p>The table below summarises per-quarter SLO attainment and the internal
    audit reference codes used by the compliance team for external reporting.</p>
    """))

    parts.append("<table><tbody>")
    parts.append("<tr><th>Quarter</th><th>SLO Attainment</th><th>Incidents</th><th>Audit Reference Code</th></tr>")

    # Several normal rows
    audit_codes = [
        ("Q1 2023", "99.92%", "12", "ARC-2023Q1-mT4x8b"),
        ("Q2 2023", "99.87%", "15", "ARC-2023Q2-jN6y2w"),
        ("Q3 2023", "99.95%", "9",  "ARC-2023Q3-qP3z7c"),
        ("Q4 2023", "99.78%", "18", "ARC-2023Q4-hR9v1d"),
        ("Q1 2024", "99.91%", "11", "ARC-2024Q1-kW5m8f"),
        ("Q2 2024", "99.83%", "16", "ARC-2024Q2-bX2n4g"),
        ("Q3 2024", "99.96%", "8",  "ARC-2024Q3-eY7p9h"),
        # This is the row with the verification code
        ("Q4 2024", "99.71%", "21", f"ARC-2024Q4-{VERIFICATION_CODE}"),
        ("Q1 2025", "99.88%", "13", "ARC-2025Q1-dZ6r3j"),
    ]
    for quarter, slo, count, code in audit_codes:
        parts.append(f"<tr><td>{quarter}</td><td>{slo}</td><td>{count}</td><td>{code}</td></tr>")
    parts.append("</tbody></table>")

    parts.append(textwrap.dedent("""\
    <h2>Document Revision History</h2>
    <p><strong>2025-03-01:</strong> Added Q1 2025 preliminary data. Updated incident
    INC-1078 remediation status.</p>
    <p><strong>2024-12-15:</strong> Added Q4 2024 data including audit reference codes
    from the compliance review. 21 incidents recorded — highest quarterly count due to
    the November platform migration.</p>
    <p><strong>2024-09-30:</strong> Added Q3 2024 data. Lowest incident quarter on
    record.</p>
    """))

    return "\n".join(parts)


if __name__ == "__main__":
    body = generate_page_body()
    # Output as JSON string for easy embedding
    print(json.dumps(body))
# trigger CI
# trigger CI
