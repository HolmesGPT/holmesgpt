#!/usr/bin/env python3
"""
Benchmark Coralogix ingestion times for different endpoints.

This script tests how long it takes for data to become queryable after ingestion
for each of the following endpoints:
1. REST API /logs/v1/singles - for logs
2. DataPrime query API - to verify logs are queryable

Run with:
    CORALOGIX_SEND_DATA_API_KEY=... CORALOGIX_QUERY_DATA_API_KEY=... python benchmark_ingestion.py

Results are printed as a summary table at the end.
"""

import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# Configuration
DOMAIN = os.environ.get("CORALOGIX_DOMAIN", "eu2.coralogix.com")
SEND_API_KEY = os.environ.get("CORALOGIX_SEND_DATA_API_KEY") or os.environ.get("CORALOGIX_SEND_API_KEY")
QUERY_API_KEY = os.environ.get("CORALOGIX_QUERY_DATA_API_KEY") or os.environ.get("CORALOGIX_API_KEY")

INGRESS_URL = f"https://ingress.{DOMAIN}"
QUERY_URL = f"https://ng-api-http.{DOMAIN}/api/v1/dataprime/query"

# Test parameters
NUM_TESTS = 10
MAX_WAIT_SECONDS = 600  # 10 minutes max wait per test
POLL_INTERVAL = 5  # seconds between polls


def send_log_singles(marker: str) -> bool:
    """Send a log via REST API /logs/v1/singles endpoint."""
    url = f"{INGRESS_URL}/logs/v1/singles"
    headers = {
        "Authorization": f"Bearer {SEND_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = [
        {
            "applicationName": "benchmark-test",
            "subsystemName": "ingestion-timing",
            "severity": 3,
            "text": f"Benchmark log with marker: {marker}",
        }
    ]

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30, verify=False)
        return resp.status_code == 200
    except Exception as e:
        print(f"  Error sending log: {e}")
        return False


def query_for_marker(marker: str) -> bool:
    """Query for a specific marker using DataPrime lucene search."""
    headers = {
        "Authorization": f"Bearer {QUERY_API_KEY}",
        "Content-Type": "application/json",
    }

    # Time range: 1 hour ago to now
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=1)

    payload = {
        "query": f"source logs | lucene '{marker}' | limit 1",
        "metadata": {
            "syntax": "QUERY_SYNTAX_DATAPRIME",
            "startDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }

    try:
        resp = requests.post(QUERY_URL, headers=headers, json=payload, timeout=60, verify=False)
        return marker in resp.text
    except Exception:
        return False


def measure_ingestion_time(test_num: int) -> float | None:
    """
    Send a log and measure how long until it's queryable.
    Returns the time in seconds, or None if it times out.
    """
    marker = f"BENCHMARK-{int(time.time())}-{test_num}"

    print(f"  Test {test_num}: Sending log with marker {marker}...")

    if not send_log_singles(marker):
        print(f"  Test {test_num}: Failed to send log")
        return None

    start_time = time.time()

    # Poll until found or timeout
    attempts = 0
    while (time.time() - start_time) < MAX_WAIT_SECONDS:
        attempts += 1
        if query_for_marker(marker):
            elapsed = time.time() - start_time
            print(f"  Test {test_num}: Found after {elapsed:.1f}s ({attempts} attempts)")
            return elapsed
        time.sleep(POLL_INTERVAL)

    print(f"  Test {test_num}: TIMEOUT after {MAX_WAIT_SECONDS}s")
    return None


def main():
    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if not SEND_API_KEY or not QUERY_API_KEY:
        print("Error: Missing API keys. Set CORALOGIX_SEND_DATA_API_KEY and CORALOGIX_QUERY_DATA_API_KEY")
        sys.exit(1)

    print("=" * 60)
    print("Coralogix Ingestion Benchmark")
    print("=" * 60)
    print(f"Domain: {DOMAIN}")
    print(f"Tests per endpoint: {NUM_TESTS}")
    print(f"Max wait per test: {MAX_WAIT_SECONDS}s")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print()

    # Test REST API /logs/v1/singles
    print("Testing REST API /logs/v1/singles endpoint...")
    print("-" * 40)

    results = []
    for i in range(1, NUM_TESTS + 1):
        result = measure_ingestion_time(i)
        if result is not None:
            results.append(result)
        # Small delay between tests to avoid rate limiting
        if i < NUM_TESTS:
            time.sleep(2)

    # Summary
    print()
    print("=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Endpoint: REST API /logs/v1/singles")
    print(f"Successful tests: {len(results)}/{NUM_TESTS}")

    if results:
        print(f"Min time: {min(results):.1f}s")
        print(f"Max time: {max(results):.1f}s")
        print(f"Mean time: {statistics.mean(results):.1f}s")
        print(f"Median time: {statistics.median(results):.1f}s")
        if len(results) > 1:
            print(f"Std dev: {statistics.stdev(results):.1f}s")
        print()
        print("All times (seconds):")
        for i, t in enumerate(results, 1):
            print(f"  Test {i}: {t:.1f}s")
    else:
        print("No successful tests!")

    print()
    print("=" * 60)

    # Return suggested timeout (95th percentile + buffer, or max + 50%)
    if results:
        suggested = max(results) * 1.5
        print(f"Suggested setup_timeout: {int(suggested)}s (max * 1.5)")
        print(f"Current recommendation: 1200s (20 minutes) for safety margin")


if __name__ == "__main__":
    main()
