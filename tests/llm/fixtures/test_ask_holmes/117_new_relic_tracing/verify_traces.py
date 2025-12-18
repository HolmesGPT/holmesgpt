#!/usr/bin/env python3
"""Verify that New Relic has received traces from all services with expected status codes."""

import argparse
import os
import sys
import time

# Add the holmes directory to the Python path so we can import new_relic_api
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../"))
)

from holmes.plugins.toolsets.newrelic.new_relic_api import NewRelicAPI


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Verify New Relic traces")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=30,
        help="Maximum number of retry attempts (default: 30)",
    )
    parser.add_argument(
        "--retry-interval",
        type=int,
        default=5,
        help="Seconds between retries (default: 5)",
    )
    args = parser.parse_args()

    # Get credentials from environment
    account_id = os.environ.get("NEW_RELIC_ACCOUNT_ID")
    api_key = os.environ.get("NEW_RELIC_API_KEY")
    is_eu = bool(os.getenv("NEW_RELIC_IS_EU", False))

    if not account_id or not api_key:
        print("Error: NEW_RELIC_ACCOUNT_ID and NEW_RELIC_API_KEY must be set")
        sys.exit(1)

    # Initialize New Relic API client
    nr_api = NewRelicAPI(api_key=api_key, account_id=account_id, is_eu_datacenter=is_eu)

    # Retry parameters
    max_attempts = args.max_attempts
    retry_interval = args.retry_interval

    for attempt in range(1, max_attempts + 1):
        print(f"Checking for traces in New Relic (attempt {attempt}/{max_attempts})...")

        try:
            # Query for distributed traces with FACET by appName and http.statusCode
            nrql_query = """SELECT count(*) FROM Span WHERE request.uri='/orders' AND appName LIKE '%checkout%' AND namespaceName = 'app-117' FACET http.statusCode SINCE 5 minutes ago"""

            result = nr_api.execute_nrql_query(nrql_query)

            # The API now returns results directly as a list
            facets = result
            print(f"Raw result from API: {result}")

            # Collect unique status codes
            status_codes = set()

            print("Current facets:")
            for facet in facets:
                # New Relic returns status code in the facet data
                status_code = facet.get("http.statusCode", facet.get("facet", ""))
                if status_code:
                    status_codes.add(str(status_code))
                print(f"  Status {status_code} - count: {facet.get('count', 0)}")

            # Check if we have all required services and status codes
            required_codes = {"200", "403", "409"}

            missing_codes = required_codes - status_codes

            if not missing_codes:
                print("✓ Found all expected status codes: 200, 403, 409")
                sys.exit(0)

            if missing_codes:
                print(f"Missing status codes: {', '.join(missing_codes)}")

        except Exception as e:
            print(f"Error querying New Relic: {e}")

        if attempt < max_attempts:
            print(f"Waiting {retry_interval} seconds before retry...")
            time.sleep(retry_interval)

    print("✗ Timeout: Did not find traces for all required services and status codes")
    sys.exit(1)


if __name__ == "__main__":
    main()
