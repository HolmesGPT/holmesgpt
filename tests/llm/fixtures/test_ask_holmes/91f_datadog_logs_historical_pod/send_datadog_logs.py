"""
Send historical logs to Datadog for a pod that no longer exists.
Simulates realistic production logs with memory issues and database connection problems.
Usage: python send_datadog_logs.py [namespace]
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


def check_environment_variables():
    """Check if required environment variables are set."""
    api_key = os.environ.get("DATADOG_API_KEY")
    app_key = os.environ.get("DATADOG_APP_KEY")

    if not api_key:
        print("ERROR: DATADOG_API_KEY environment variable is not set")
        sys.exit(1)

    if not app_key:
        print("ERROR: DATADOG_APP_KEY environment variable is not set")
        sys.exit(1)

    return api_key, app_key


def get_recent_timestamps():
    """Calculate timestamps for 9 hours ago."""
    now = datetime.now(timezone.utc)
    nine_hours_ago = now - timedelta(hours=9)

    # Set to specific times around 9 hours ago
    base_time = nine_hours_ago

    timestamps = {
        "start_time": int((base_time - timedelta(minutes=5)).timestamp() * 1000),
        "critical_time": int(base_time.timestamp() * 1000),
        "post_critical": int((base_time + timedelta(minutes=5)).timestamp() * 1000),
        "end_time": int((base_time + timedelta(minutes=10)).timestamp() * 1000),
    }

    return timestamps


def send_log(api_key, namespace, timestamp, level, message, additional_tags=""):
    """Send a single log entry to Datadog."""
    datadog_site = os.environ.get(
        "DATADOG_SITE", "https://http-intake.logs.datadoghq.eu"
    )

    # Construct the log entry
    log_entry = {
        "ddsource": "kubernetes",
        "ddtags": f"env:production,kube_namespace:{namespace},pod_name:coral-reef-7b9f4fd5c9-xk2lm,container_name:coral-reef{additional_tags}",
        "hostname": "node-03.k8s.cluster",
        "message": message,
        "service": "coral-reef",
        "status": level,
        "timestamp": timestamp,
    }

    # Prepare the request
    url = f"{datadog_site}/v1/input"
    data = json.dumps([log_entry]).encode("utf-8")
    headers = {"Content-Type": "application/json", "DD-API-KEY": api_key}

    req = urllib.request.Request(url, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            response_body = response.read()
            if response.status == 200:
                print(f"Sent log: {level} - {message[:50]}...")
            else:
                print(
                    f"ERROR: Failed sending log to Datadog (level={level}). Status: {response.status}"
                )
                print(f"Response: {response_body}")
                sys.exit(1)
    except urllib.error.HTTPError as e:
        print(
            f"ERROR: Failed sending log to Datadog (level={level}). HTTP Error: {e.code}"
        )
        error_body = e.read().decode("utf-8", errors="ignore")
        print(f"Error response: {error_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(
            f"ERROR: Failed sending log to Datadog (level={level}). URL Error: {str(e)}"
        )
        sys.exit(1)
    except Exception as e:
        print(
            f"ERROR: Failed sending log to Datadog (level={level}). Error: {type(e).__name__}: {str(e)}"
        )
        sys.exit(1)


def query_logs(api_key, app_key, namespace, from_timestamp, to_timestamp):
    """Query Datadog API for logs."""
    datadog_api_url = os.environ.get("DATADOG_API_URL", "https://api.datadoghq.eu")

    # Prepare the query
    query_payload = {
        "filter": {
            "from": str(from_timestamp),
            "to": str(to_timestamp),
            "query": f"kube_namespace:{namespace} pod_name:coral-reef-7b9f4fd5c9-xk2lm",
            "indexes": ["*"],
        },
        "sort": "-timestamp",
        "page": {"limit": 1},
    }

    url = f"{datadog_api_url}/api/v2/logs/events/search"
    data = json.dumps(query_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
    }

    req = urllib.request.Request(url, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response_data = json.loads(response.read().decode("utf-8"))
            return response_data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_body)
            if "errors" in error_json:
                print("ERROR: Failed to query logs from Datadog API:")
                print(json.dumps(error_json, indent=2))
                print(
                    "\nThis means logs were sent but cannot be queried. Possible issues:"
                )
                print("- API key may not have logs_read_data permission")
                print("- Logs may not be indexed yet (try increasing sleep time)")
                print("- Wrong API endpoint for your Datadog region")
                sys.exit(1)
        except:  # noqa: E722
            pass
        raise


def verify_logs_with_retry(api_key, app_key, namespace, from_timestamp, to_timestamp):
    """Verify logs are accessible with retry logic."""
    max_retries = 12
    retry_delay = 10
    initial_wait = 5

    print(f"\nWaiting {initial_wait} seconds before first check...")
    time.sleep(initial_wait)

    for attempt in range(1, max_retries + 1):
        print(f"Attempt {attempt}/{max_retries} to query logs...")

        try:
            response = query_logs(
                api_key, app_key, namespace, from_timestamp, to_timestamp
            )
            log_count = len(response.get("data", []))

            if log_count > 0:
                print(f"✓ Verified: Found {log_count} log(s) in Datadog")
                print("Logs are successfully accessible via Datadog API")
                return True

            if attempt < max_retries:
                print(
                    f"No logs found yet. Waiting {retry_delay} seconds before retry..."
                )
                time.sleep(retry_delay)
            else:
                print(
                    "ERROR: No logs found in Datadog query response after all retries"
                )
                print(f"Response: {json.dumps(response)}")
                print(
                    "\nLogs may not be indexed yet or query parameters may be incorrect"
                )
                return False

        except Exception as e:
            print(f"ERROR: Exception while querying: {str(e)}")
            if attempt < max_retries:
                print(f"Waiting {retry_delay} seconds before retry...")
                time.sleep(retry_delay)
            else:
                return False

    return False


def main():
    """Main function to send logs and verify they're accessible."""
    # Get namespace from command line or use default
    namespace = sys.argv[1] if len(sys.argv) > 1 else "app-91f"

    # Check environment variables
    api_key, app_key = check_environment_variables()

    # Get timestamps
    timestamps = get_recent_timestamps()

    print(
        f"Sending recent logs to Datadog for pod coral-reef in namespace {namespace}..."
    )
    print(
        "Logs are from approximately 9 hours ago to ensure they are within Datadog's indexing window"
    )

    # Define all log entries
    log_entries = [
        # Normal operation logs (start_time - 5 minutes)
        (timestamps["start_time"], "INFO", "Starting api-gateway service v2.3.1", ""),
        (
            timestamps["start_time"] + 1000,
            "INFO",
            "Initializing database connection pool with size=50",
            "",
        ),
        (
            timestamps["start_time"] + 2000,
            "INFO",
            "Connected to database successfully",
            "",
        ),
        (
            timestamps["start_time"] + 3000,
            "INFO",
            "HTTP server listening on port 8080",
            "",
        ),
        (timestamps["start_time"] + 10000, "INFO", "Health check endpoint ready", ""),
        (
            timestamps["start_time"] + 15000,
            "INFO",
            "Serving traffic normally, current memory: 412MB",
            "",
        ),
        # Early warning signs (critical_time - 2 minutes)
        (
            timestamps["critical_time"] - 120000,
            "WARN",
            "Memory usage increasing: 680MB / 1024MB",
            ",memory:warning",
        ),
        (
            timestamps["critical_time"] - 90000,
            "INFO",
            "Processing batch job: 1000 records",
            "",
        ),
        (
            timestamps["critical_time"] - 60000,
            "WARN",
            "Database connection pool usage: 45/50 connections",
            ",db:warning",
        ),
        (
            timestamps["critical_time"] - 30000,
            "WARN",
            "Memory usage high: 850MB / 1024MB",
            ",memory:warning",
        ),
        # Critical period (critical_time)
        (
            timestamps["critical_time"],
            "ERROR",
            "Database connection pool exhausted - MaxConnectionsReached: All 50 connections in use",
            ",db:error",
        ),
        (
            timestamps["critical_time"] + 1000,
            "ERROR",
            "Failed to acquire database connection - timeout after 30s",
            ",db:error",
        ),
        (
            timestamps["critical_time"] + 2000,
            "WARN",
            "Memory usage critical: 980MB / 1024MB",
            ",memory:critical",
        ),
        (
            timestamps["critical_time"] + 3000,
            "ERROR",
            "Request handler failed: java.lang.OutOfMemoryError: Java heap space",
            ",memory:error",
        ),
        (
            timestamps["critical_time"] + 4000,
            "ERROR",
            "Cannot allocate memory for new request buffer",
            ",memory:error",
        ),
        (
            timestamps["critical_time"] + 5000,
            "ERROR",
            "Memory allocation failed in request processor",
            ",memory:error",
        ),
        # Memory leak evidence
        (
            timestamps["critical_time"] + 10000,
            "ERROR",
            "RequestHandler memory leak detected: 512MB unreleased buffers",
            ",memory:leak",
        ),
        (
            timestamps["critical_time"] + 11000,
            "ERROR",
            "Failed to process request: java.lang.OutOfMemoryError",
            ",memory:error",
        ),
        (
            timestamps["critical_time"] + 12000,
            "CRITICAL",
            "JVM heap dump triggered due to OutOfMemoryError",
            ",memory:critical",
        ),
        # OOM Kill (critical_time + 2 minutes)
        (
            timestamps["critical_time"] + 120000,
            "ERROR",
            "Application terminating: OutOfMemoryError",
            ",memory:fatal",
        ),
        (
            timestamps["critical_time"] + 121000,
            "FATAL",
            "Process killed - OOM killer triggered (memory usage: 1024MB/1024MB)",
            ",memory:fatal,oom:kill",
        ),
        # Additional context logs
        (
            timestamps["critical_time"] + 60000,
            "ERROR",
            "500 errors returned to clients - service unavailable",
            ",http:error",
        ),
        (
            timestamps["critical_time"] + 70000,
            "ERROR",
            "Health check failed - service unresponsive",
            ",health:failed",
        ),
        (
            timestamps["critical_time"] + 80000,
            "ERROR",
            "Circuit breaker opened due to repeated failures",
            ",circuit:open",
        ),
        # Analysis hints (logs that help identify root cause)
        (
            timestamps["critical_time"] - 180000,
            "INFO",
            "Started processing large batch import job ID: batch-2943",
            "",
        ),
        (
            timestamps["critical_time"] - 170000,
            "DEBUG",
            "Batch import: loading 50000 records into memory",
            "",
        ),
        (
            timestamps["critical_time"] - 160000,
            "DEBUG",
            "RequestHandler: buffer pool size increased to 256MB",
            "",
        ),
        (
            timestamps["critical_time"] - 150000,
            "WARN",
            "GC overhead limit: 98% time spent in garbage collection",
            ",gc:warning",
        ),
    ]

    # Send all logs
    for timestamp, level, message, tags in log_entries:
        send_log(api_key, namespace, timestamp, level, message, tags)

    print("\nSuccessfully sent recent logs to Datadog")
    print(f"Pod coral-reef-7b9f4fd5c9-xk2lm in namespace {namespace}")
    print(
        "Logs simulate memory exhaustion and database connection pool issues from ~9 hours ago"
    )

    # Verify logs are queryable
    print("\nVerifying logs are accessible via Datadog query API...")

    # Query time range with buffer
    from_timestamp = timestamps["start_time"] - 60000
    to_timestamp = timestamps["end_time"] + 60000

    if verify_logs_with_retry(
        api_key, app_key, namespace, from_timestamp, to_timestamp
    ):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
