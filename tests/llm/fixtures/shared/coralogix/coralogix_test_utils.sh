#!/bin/bash
# Shared utilities for Coralogix eval tests
# Source this file at the start of before_test scripts:
#   source ../../shared/coralogix_test_utils.sh
#
# Required tools:
#   jq - for JSON construction and escaping
#
# Required environment variables:
#   CORALOGIX_SEND_API_KEY - API key with SendData permissions (for ingestion)
#   CORALOGIX_API_KEY - API key with DataQuerying permissions (for queries)
#
# Domain is hardcoded to eu2.coralogix.com
#
# Note: Coralogix uses separate API keys for sending vs querying data.
# See: https://coralogix.com/docs/user-guides/account-management/api-keys/api-keys/

# Check for required tools
if ! command -v jq &> /dev/null; then
  echo "❌ jq is required but not installed. Please install jq."
  exit 1
fi

# Validate Coralogix environment variables
cx_validate_env() {
  local missing=()

  if [ -z "$CORALOGIX_SEND_API_KEY" ]; then
    missing+=("CORALOGIX_SEND_API_KEY")
  fi

  if [ -z "$CORALOGIX_API_KEY" ]; then
    missing+=("CORALOGIX_API_KEY")
  fi

  if [ ${#missing[@]} -gt 0 ]; then
    echo "❌ Missing required environment variables: ${missing[*]}"
    exit 1
  fi

  echo "✅ Coralogix environment validated"
}

# Hardcoded Coralogix domain
CORALOGIX_DOMAIN="eu2.coralogix.com"

# Get the ingestion endpoint for sending logs
# Usage: INGRESS_URL=$(cx_ingress_url)
cx_ingress_url() {
  echo "https://ingress.${CORALOGIX_DOMAIN}"
}

# Get the DataPrime query endpoint
# Usage: QUERY_URL=$(cx_query_url)
cx_query_url() {
  echo "https://ng-api-http.${CORALOGIX_DOMAIN}/api/v1/dataprime/query"
}

# Combined setup: validate env
cx_setup() {
  cx_validate_env
}
