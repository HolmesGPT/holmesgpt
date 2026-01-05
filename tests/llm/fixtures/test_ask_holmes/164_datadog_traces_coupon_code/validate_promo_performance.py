"""
Validate that checkout URLs with promo codes are at least 3x slower than those without.
Uses Datadog Spans Analytics API to aggregate performance data.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


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


def query_spans_aggregate(api_key, app_key):
    """Query Datadog Spans Analytics API for checkout performance data."""
    datadog_api_url = os.environ.get("DATADOG_API_URL", "https://api.datadoghq.eu")

    # Prepare the aggregate query - wrap in data/attributes structure
    query_payload = {
        "data": {
            "type": "aggregate_request",
            "attributes": {
                "filter": {
                    "from": "now-1h",
                    "to": "now",
                    "query": "service:checkout @http.url:*checkout* kube_namespace:app-164",
                },
                "compute": [
                    {"aggregation": "count", "type": "total"},
                    {"aggregation": "avg", "metric": "@duration", "type": "total"},
                ],
                "group_by": [{"facet": "@http.url", "limit": 10}],
            },
        }
    }

    url = f"{datadog_api_url}/api/v2/spans/analytics/aggregate"
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
        print(f"ERROR: Failed to query spans from Datadog API. HTTP Error: {e.code}")
        print(f"Error response: {error_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Failed to query spans from Datadog API. URL Error: {str(e)}")
        sys.exit(1)
    except Exception as e:
        print(
            f"ERROR: Failed to query spans from Datadog API. Error: {type(e).__name__}: {str(e)}"
        )
        sys.exit(1)


def has_promo_code(url):
    """Check if a URL contains a promo code parameter."""
    # Check for coupon parameter with a value (not empty)
    if "coupon=" in url:
        # Extract the value after coupon=
        parts = url.split("coupon=")
        if len(parts) > 1 and parts[1].strip():
            return True
    return "promo_code=" in url or "promocode=" in url.lower()


def validate_promo_performance():
    """Main validation logic."""
    # Check environment variables
    api_key, app_key = check_environment_variables()

    print("Querying Datadog Spans Analytics for checkout endpoint performance...")

    # Retry intervals in seconds
    retry_intervals = [30, 60, 90, 120, 150]
    attempt = 0
    buckets = []

    while attempt <= len(retry_intervals):
        # Query the API
        response = query_spans_aggregate(api_key, app_key)

        # Parse results - handle the actual response format
        if "data" not in response:
            print("ERROR: No data in response from Datadog API")
            print(f"Response: {json.dumps(response, indent=2)}")
            sys.exit(1)

        buckets = response["data"]

        if buckets:
            # We have data, proceed
            break

        # No buckets returned
        if attempt < len(retry_intervals):
            wait_time = retry_intervals[attempt]
            print(
                f"No data returned from Datadog API (attempt {attempt + 1}/{len(retry_intervals) + 1})"
            )
            print("This could mean no checkout spans exist yet")
            print(f"Retrying in {wait_time} seconds...")
            time.sleep(wait_time)
            attempt += 1
        else:
            # All retries exhausted
            print("ERROR: No data returned from Datadog API after all retries")
            print(f"Response: {json.dumps(response, indent=2)}")
            print("This could mean no checkout spans exist in the last hour")
            sys.exit(1)

    # Separate URLs into promo and non-promo groups
    promo_urls = []
    non_promo_urls = []

    print("\nAnalyzing checkout endpoint performance by URL:")
    print("-" * 60)

    for bucket in buckets:
        # Handle the actual response structure
        if "attributes" not in bucket:
            continue

        attributes = bucket["attributes"]
        if "by" not in attributes or "@http.url" not in attributes["by"]:
            continue

        url = attributes["by"]["@http.url"]
        computes = attributes.get("compute", {})

        # Extract count (c0) and average duration (c1) from compute
        count = computes.get("c0", 0)
        avg_duration = computes.get("c1", 0)

        if avg_duration is None:
            print(f"WARNING: No duration data for URL: {url}")
            continue

        # Convert duration from nanoseconds to milliseconds
        avg_duration_ms = avg_duration / 1_000_000

        url_info = {
            "url": url,
            "count": count or 0,
            "avg_duration_ns": avg_duration,
            "avg_duration_ms": avg_duration_ms,
        }

        if has_promo_code(url):
            promo_urls.append(url_info)
            print(f"PROMO URL: {url}")
            print(f"  Count: {count}, Avg Duration: {avg_duration_ms:.2f}ms")
        else:
            non_promo_urls.append(url_info)
            print(f"Regular URL: {url}")
            print(f"  Count: {count}, Avg Duration: {avg_duration_ms:.2f}ms")

    print("-" * 60)

    # Validate we have both types of URLs
    if not promo_urls:
        print("ERROR: No URLs with promo codes found")
        print("Expected to find checkout URLs containing 'promo_code=' parameter")
        sys.exit(1)

    if not non_promo_urls:
        print("ERROR: No URLs without promo codes found")
        print("Expected to find checkout URLs without promo code parameters")
        sys.exit(1)

    # Calculate average durations
    promo_avg = sum(u["avg_duration_ns"] for u in promo_urls) / len(promo_urls)
    non_promo_avg = sum(u["avg_duration_ns"] for u in non_promo_urls) / len(
        non_promo_urls
    )

    promo_avg_ms = promo_avg / 1_000_000
    non_promo_avg_ms = non_promo_avg / 1_000_000

    print("\nAggregate Performance Analysis:")
    print(f"  Promo Code URLs: {len(promo_urls)} unique URLs")
    print(f"    Average Duration: {promo_avg_ms:.2f}ms")
    print(f"  Non-Promo URLs: {len(non_promo_urls)} unique URLs")
    print(f"    Average Duration: {non_promo_avg_ms:.2f}ms")

    # Calculate performance ratio
    ratio = promo_avg / non_promo_avg if non_promo_avg > 0 else 0

    print(f"\nPerformance Ratio: {ratio:.2f}x")
    print(f"(Promo code URLs are {ratio:.2f}x slower than regular URLs)")

    # Validate the ratio
    required_ratio = 3.0
    if ratio >= required_ratio:
        print(
            f"\n✅ VALIDATION PASSED: Promo code URLs are at least {required_ratio}x slower"
        )
        return True
    else:
        print(f"\n❌ VALIDATION FAILED: Promo code URLs are only {ratio:.2f}x slower")
        print(f"   Expected at least {required_ratio}x slower")
        return False


def main():
    """Main function."""
    success = validate_promo_performance()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
