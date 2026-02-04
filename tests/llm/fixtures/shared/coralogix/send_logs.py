#!/usr/bin/env python3
"""
Send test logs to Coralogix via REST API.

Usage:
    python send_logs.py --app-name order-service-abc123 --subsystem payment-api \
        --run-id abc123 --error-codes ERR-7291,ERR-4058,ERR-9463

Environment variables (alternative to CLI args):
    CORALOGIX_DOMAIN - Coralogix domain (default: eu2.coralogix.com)
    CORALOGIX_SEND_API_KEY - API key with SendData permissions (for ingestion)
    SSL_VERIFY - Set to 'false' to disable SSL verification

Note: Coralogix uses separate API keys for sending vs querying data.
See: https://coralogix.com/docs/user-guides/account-management/api-keys/api-keys/
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
import urllib3


def send_logs(
    domain: str,
    api_key: str,
    app_name: str,
    subsystem: str,
    run_id: str,
    error_codes: list[str],
    verify_ssl: bool = True,
    verbose: bool = False,
) -> bool:
    """Send test logs to Coralogix via REST API /logs/v1/singles endpoint."""

    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    endpoint = f"https://ingress.{domain}/logs/v1/singles"

    # Build log entries - each includes EVAL_RUN_ID in text for queryability
    # Coralogix /singles API expects an array of log entries
    # See: https://coralogix.com/docs/developer-portal/apis/log-ingestion/coralogix-rest-api-singles/
    log_entries = [
        {
            "applicationName": app_name,
            "subsystemName": subsystem,
            "severity": 3,  # INFO
            "text": f"[{run_id}] Service started successfully - {subsystem} initialized",
            "timestamp": int(time.time() * 1000),
        },
        {
            "applicationName": app_name,
            "subsystemName": subsystem,
            "severity": 5,  # ERROR
            "text": f"[{run_id}] Database connection timeout - {error_codes[0]}: Failed to connect to primary database after 30s",
            "timestamp": int(time.time() * 1000),
        },
        {
            "applicationName": app_name,
            "subsystemName": subsystem,
            "severity": 4,  # WARNING
            "text": f"[{run_id}] Retrying database connection attempt 1",
            "timestamp": int(time.time() * 1000),
        },
        {
            "applicationName": app_name,
            "subsystemName": subsystem,
            "severity": 5,  # ERROR
            "text": f"[{run_id}] Payment validation failed - {error_codes[1]}: Invalid card number format for user U-12345",
            "timestamp": int(time.time() * 1000),
        },
        {
            "applicationName": app_name,
            "subsystemName": subsystem,
            "severity": 3,  # INFO
            "text": f"[{run_id}] Processing order batch complete - 150 orders processed",
            "timestamp": int(time.time() * 1000),
        },
        {
            "applicationName": app_name,
            "subsystemName": subsystem,
            "severity": 5,  # ERROR
            "text": f"[{run_id}] Stock sync failed - {error_codes[2]}: External inventory API returned 503",
            "timestamp": int(time.time() * 1000),
        },
    ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "CX-Application-Name": app_name,
        "CX-Subsystem-Name": subsystem,
    }

    # Debug output
    print(f"{'=' * 60}")
    print("CORALOGIX LOG INGESTION DEBUG INFO")
    print(f"{'=' * 60}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Endpoint: {endpoint}")
    print(f"Application: {app_name}")
    print(f"Subsystem: {subsystem}")
    print(f"Run ID: {run_id}")
    print(f"Error codes: {error_codes}")
    print(f"SSL verify: {verify_ssl}")
    print(f"Log entries count: {len(log_entries)}")
    print(f"API key (first 8 chars): {api_key[:8]}...")
    print(f"API key length: {len(api_key)}")

    if verbose:
        print("\n--- Request Headers ---")
        for k, v in headers.items():
            if k == "Authorization":
                print(f"  {k}: Bearer {api_key[:8]}...")
            else:
                print(f"  {k}: {v}")

        print("\n--- Request Body (JSON) ---")
        print(json.dumps(log_entries, indent=2))

    print(f"\n{'=' * 60}")
    print("SENDING REQUEST...")
    print(f"{'=' * 60}")

    try:
        start_time = time.time()
        response = requests.post(
            endpoint,
            json=log_entries,
            headers=headers,
            verify=verify_ssl,
            timeout=60,
        )
        elapsed = time.time() - start_time

        print("\n--- Response Details ---")
        print(f"HTTP Status Code: {response.status_code}")
        print(f"Response Time: {elapsed:.2f}s")
        print("Response Headers:")
        for k, v in response.headers.items():
            print(f"  {k}: {v}")
        print(f"Response Body: {response.text[:1000] if response.text else '(empty)'}")

        if response.status_code == 200:
            # Check for "Dropped" in response which indicates logs were rejected
            if "Dropped" in response.text:
                print("\n❌ FAILURE: Logs were dropped by Coralogix")
                print(f"   Full response: {response.text}")
                return False

            print(f"\n✅ SUCCESS: Logs sent to {app_name}/{subsystem}")
            print("   HTTP 200 received")
            print(f"   {len(log_entries)} log entries ingested")
            return True
        else:
            print(f"\n❌ FAILURE: HTTP {response.status_code}")
            print(f"   Response: {response.text}")
            if response.status_code == 401:
                print(
                    "   HINT: 401 Unauthorized - Check CORALOGIX_SEND_API_KEY is valid"
                )
            elif response.status_code == 403:
                print("   HINT: 403 Forbidden - API key may lack SendData permissions")
            elif response.status_code == 400:
                print("   HINT: 400 Bad Request - Check JSON payload format")
            return False

    except requests.exceptions.Timeout:
        print("\n❌ FAILURE: Request timed out after 60s")
        print("   HINT: Check network connectivity to Coralogix")
        return False
    except requests.exceptions.SSLError as e:
        print("\n❌ FAILURE: SSL Error")
        print(f"   Error: {e}")
        print("   HINT: Try setting SSL_VERIFY=false")
        return False
    except requests.exceptions.ConnectionError as e:
        print("\n❌ FAILURE: Connection Error")
        print(f"   Error: {e}")
        print("   HINT: Check network connectivity and endpoint URL")
        return False
    except requests.exceptions.RequestException as e:
        print("\n❌ FAILURE: Request failed")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Error: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   Response status: {e.response.status_code}")
            print(f"   Response body: {e.response.text}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Send test logs to Coralogix")
    parser.add_argument(
        "--domain",
        default=os.environ.get("CORALOGIX_DOMAIN", "eu2.coralogix.com"),
        help="Coralogix domain (e.g., eu2.coralogix.com)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("CORALOGIX_SEND_API_KEY"),
        help="Coralogix Send-Your-Data API key (SendData permissions)",
    )
    parser.add_argument("--app-name", required=True, help="Application name")
    parser.add_argument("--subsystem", required=True, help="Subsystem name")
    parser.add_argument(
        "--run-id", required=True, help="Unique run ID for test isolation (EVAL_RUN_ID)"
    )
    parser.add_argument(
        "--error-codes",
        required=True,
        help="Comma-separated error codes to inject (e.g., ERR-7291,ERR-4058,ERR-9463)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: --api-key or CORALOGIX_SEND_API_KEY required")
        sys.exit(1)

    # Parse error codes
    error_codes = [code.strip() for code in args.error_codes.split(",")]
    if len(error_codes) < 3:
        print(f"ERROR: Need at least 3 error codes, got {len(error_codes)}")
        sys.exit(1)

    # Check SSL verification setting from environment
    verify_ssl = os.environ.get("SSL_VERIFY", "true").lower() not in (
        "false",
        "0",
        "no",
    )

    success = send_logs(
        domain=args.domain,
        api_key=args.api_key,
        app_name=args.app_name,
        subsystem=args.subsystem,
        run_id=args.run_id,
        error_codes=error_codes,
        verify_ssl=verify_ssl,
        verbose=args.verbose,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
