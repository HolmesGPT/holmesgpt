"""
Inject Datadog logs anchored to a hypothetical alert time, for the
`260_alert_anchored_*` ask_holmes evals.

The eval's user prompt cites an alert at time T. The lost prompt
(_fetch_logs.jinja2) instructed Holmes to scope log queries to ~5 min
before T. These scenarios validate Holmes still does this without the
prompt, by making the answer only retrievable from a window around T.

Each scenario writes a small, deterministic set of logs to Datadog using
a deterministic pod_name derived from the namespace. Scenarios:

  basic         - Single error code at T. No noise.
  decoys        - Error at T, plus decoy errors at T-3h and T+3h.
  spam          - Heavy info-log spam across an 8h window; one error at T.
  old_decoy     - Spam + error at T; an older error of a different scenario
                  several hours BEFORE T. Catches too-far-back queries.
  recent_decoy  - Spam + error at T (hours ago); a different recent error
                  in the last hour. Catches "use recent defaults" queries.

Usage:
  python3 inject_datadog_alert_logs.py <namespace> <scenario> <alert_minutes_ago>

The script prints the ISO timestamp of T to stdout (last line) so the
test_case.yaml can substitute it into the user prompt.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


def env_or_die(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: {name} environment variable is not set", file=sys.stderr)
        sys.exit(1)
    return val


def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def send_log(api_key: str, namespace: str, pod_name: str, service: str,
             timestamp_ms: int, level: str, message: str) -> None:
    site = os.environ.get("DATADOG_SITE", "https://http-intake.logs.us5.datadoghq.com")
    log_entry = {
        "ddsource": "kubernetes",
        "ddtags": f"env:production,kube_namespace:{namespace},pod_name:{pod_name},container_name:{service}",
        "hostname": f"node-{namespace}.k8s.cluster",
        "message": message,
        "service": service,
        "status": level,
        "timestamp": timestamp_ms,
    }
    url = f"{site}/v1/input"
    req = urllib.request.Request(
        url,
        data=json.dumps([log_entry]).encode("utf-8"),
        headers={"Content-Type": "application/json", "DD-API-KEY": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print(f"ERROR: HTTP {resp.status} sending log", file=sys.stderr)
                sys.exit(1)
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} sending log: {e.read().decode('utf-8', 'ignore')}", file=sys.stderr)
        sys.exit(1)


def query_log_count(api_key: str, app_key: str, namespace: str,
                    from_ms: int, to_ms: int) -> int:
    api_url = os.environ.get("DATADOG_API_URL", "https://api.us5.datadoghq.com")
    payload = {
        "filter": {
            "from": str(from_ms),
            "to": str(to_ms),
            "query": f"kube_namespace:{namespace}",
            "indexes": ["*"],
        },
        "sort": "-timestamp",
        "page": {"limit": 200},
    }
    req = urllib.request.Request(
        f"{api_url}/api/v2/logs/events/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return len(json.loads(resp.read().decode("utf-8")).get("data", []))


def wait_for_indexing(api_key: str, app_key: str, namespace: str,
                      from_ms: int, to_ms: int, min_count: int) -> None:
    count = 0
    for attempt in range(1, 13):
        time.sleep(5 if attempt == 1 else 10)
        try:
            count = query_log_count(api_key, app_key, namespace, from_ms, to_ms)
        except Exception as e:
            print(f"Attempt {attempt}: query error: {e}")
            continue
        print(f"Attempt {attempt}: indexed {count}/{min_count}")
        if count >= min_count:
            return
    print(f"ERROR: only {count} of {min_count} logs indexed after retries", file=sys.stderr)
    sys.exit(1)


# ---- Scenarios ------------------------------------------------------------

def scenario_basic(T: datetime, service: str):
    """Single error window at T. No noise."""
    yield (T - timedelta(minutes=5), "INFO", f"Starting {service} v2.3.1")
    yield (T - timedelta(minutes=4), "INFO", "Database connection pool initialized")
    yield (T - timedelta(minutes=3), "INFO", "HTTP server listening on :8080")
    yield (T - timedelta(seconds=30), "WARN", "Memory usage approaching threshold: 870MB/1024MB")
    yield (T, "ERROR", "ALRT-NRRW-A1B2C3: Failed to acquire database connection - pool exhausted (50/50 in use)")
    yield (T + timedelta(seconds=15), "ERROR", "ALRT-NRRW-A1B2C3: Request handler aborted - upstream timeout after 30s")
    yield (T + timedelta(seconds=45), "ERROR", "ALRT-NRRW-A1B2C3: Health check failing - service marked unhealthy")


def scenario_decoys(T: datetime, service: str):
    """Error at T plus unrelated errors at T-3h and T+3h."""
    # Decoy 1 - 3h before T
    yield (T - timedelta(hours=3), "ERROR", "ALRT-DCY1-X1: Scheduled backup retry started")
    yield (T - timedelta(hours=3) + timedelta(seconds=30), "ERROR", "ALRT-DCY1-X1: Backup completed after 2 retries, no data loss")
    # Correct - at T
    yield (T - timedelta(seconds=30), "WARN", "Memory usage critical: 980MB/1024MB")
    yield (T, "ERROR", "ALRT-CORR-Y2: Database connection pool exhausted - cannot accept new requests")
    yield (T + timedelta(seconds=20), "ERROR", "ALRT-CORR-Y2: 47 requests queued, connection timeout")
    # Decoy 2 - 3h after T
    yield (T + timedelta(hours=3), "ERROR", "ALRT-DCY2-Z3: Cache rebuild scheduled for next maintenance window")
    yield (T + timedelta(hours=3) + timedelta(seconds=20), "ERROR", "ALRT-DCY2-Z3: Cache size estimate: 2.3GB")


def scenario_spam(T: datetime, service: str):
    """100 spammy info logs over an 8h window; one ERROR cluster at T."""
    # Spam spread across T-4h .. T+4h
    for i in range(50):
        offset_min = -240 + (i * 480 // 50)  # span 8h
        yield (
            T + timedelta(minutes=offset_min, seconds=(i * 7) % 60),
            "INFO",
            f"Heartbeat ok req_id=req_{i:04d} latency_ms={50 + (i % 30)}",
        )
    for i in range(50):
        offset_min = -240 + (i * 480 // 50) + 3
        yield (
            T + timedelta(minutes=offset_min, seconds=((i * 11) % 60)),
            "DEBUG",
            f"Cache lookup key=user_{i % 20}_session miss=false",
        )
    # The actual error - narrow 1-min window at T
    yield (T, "ERROR", "ALRT-SPAM-N4M5P6: Authentication service returned 503, circuit breaker opened")
    yield (T + timedelta(seconds=20), "ERROR", "ALRT-SPAM-N4M5P6: All upstream auth replicas marked unhealthy")
    yield (T + timedelta(seconds=50), "ERROR", "ALRT-SPAM-N4M5P6: Request /api/checkout failed - dependency degraded")


def scenario_old_decoy(T: datetime, service: str):
    """Spam + error at T; an unrelated old error 7h before T (too-far-back trap)."""
    # Old decoy errors well before T
    yield (T - timedelta(hours=7), "ERROR", "ALRT-OLDB-AAAA: Disk usage 95% on node-storage-1 (resolved 6h ago)")
    yield (T - timedelta(hours=7) + timedelta(seconds=30), "ERROR", "ALRT-OLDB-AAAA: Garbage collection succeeded, disk usage now 62%")
    # Spam spread across T-8h..T+1h
    for i in range(40):
        offset_min = -480 + (i * 540 // 40)
        yield (
            T + timedelta(minutes=offset_min, seconds=(i * 13) % 60),
            "INFO",
            f"Heartbeat ok req_id=req_{i:04d}",
        )
    # The actual error at T
    yield (T - timedelta(seconds=20), "WARN", "Memory usage critical: 970MB/1024MB")
    yield (T, "ERROR", "ALRT-RCNT-Q7R8S9: OutOfMemoryError in payment-processor request handler")
    yield (T + timedelta(seconds=15), "ERROR", "ALRT-RCNT-Q7R8S9: Pod evicted by kubelet due to memory pressure")


def scenario_recent_decoy(T: datetime, service: str):
    """Spam + error at T (hours ago); unrelated recent error within last hour."""
    # Old error - at T
    yield (T - timedelta(seconds=30), "WARN", "Connection pool utilization 92%")
    yield (T, "ERROR", "ALRT-OLDR-T1U2V3: TLS handshake failed - certificate expired for upstream-svc.internal")
    yield (T + timedelta(seconds=20), "ERROR", "ALRT-OLDR-T1U2V3: Falling back to plaintext rejected by policy")
    # Spam everywhere
    for i in range(40):
        offset_min = -480 + (i * 540 // 40)
        yield (
            T + timedelta(minutes=offset_min, seconds=(i * 17) % 60),
            "INFO",
            f"Heartbeat ok req_id=req_{i:04d}",
        )
    # Recent decoy errors within last hour
    now = datetime.now(timezone.utc)
    yield (now - timedelta(minutes=20), "ERROR", "ALRT-NEWR-CCCC: Rate limit hit on external geocoding API (recent, unrelated)")
    yield (now - timedelta(minutes=15), "ERROR", "ALRT-NEWR-CCCC: Backing off geocoder for 5 minutes")


SCENARIOS = {
    "basic": scenario_basic,
    "decoys": scenario_decoys,
    "spam": scenario_spam,
    "old_decoy": scenario_old_decoy,
    "recent_decoy": scenario_recent_decoy,
}


def main():
    if len(sys.argv) != 4:
        print("Usage: inject_datadog_alert_logs.py <namespace> <scenario> <alert_minutes_ago>", file=sys.stderr)
        sys.exit(1)
    namespace, scenario, alert_minutes_ago_str = sys.argv[1:4]
    if scenario not in SCENARIOS:
        print(f"Unknown scenario {scenario!r}. Valid: {list(SCENARIOS)}", file=sys.stderr)
        sys.exit(1)
    alert_minutes_ago = int(alert_minutes_ago_str)

    api_key = env_or_die("DATADOG_API_KEY")
    app_key = env_or_die("DATADOG_APP_KEY")

    now = datetime.now(timezone.utc)
    T = now - timedelta(minutes=alert_minutes_ago)
    if alert_minutes_ago > 17 * 60:
        print(f"ERROR: alert_minutes_ago={alert_minutes_ago} exceeds Datadog's ~18h indexing window", file=sys.stderr)
        sys.exit(1)

    pod_name = f"{namespace}-app-7b9f4fd5c9-x1k2m"
    service = f"{namespace}-svc"

    entries = list(SCENARIOS[scenario](T, service))
    earliest = min(dt for dt, _, _ in entries)
    latest = max(dt for dt, _, _ in entries)

    print(f"Injecting {len(entries)} logs for scenario={scenario} into namespace={namespace}")
    print(f"  Alert time T:   {T.isoformat()}")
    print(f"  Earliest log:   {earliest.isoformat()}")
    print(f"  Latest log:     {latest.isoformat()}")

    for dt, level, message in entries:
        send_log(api_key, namespace, pod_name, service, ts_ms(dt), level, message)

    # Wait for indexing across the full span (+/- 5 min margin)
    from_ms = ts_ms(earliest - timedelta(minutes=5))
    to_ms = ts_ms(latest + timedelta(minutes=5))
    wait_for_indexing(api_key, app_key, namespace, from_ms, to_ms, min_count=len(entries))

    # Print the alert ISO timestamp on the LAST line so before_test can capture it
    print(f"ALERT_TIME={T.replace(microsecond=0).isoformat().replace('+00:00', 'Z')}")


if __name__ == "__main__":
    main()
