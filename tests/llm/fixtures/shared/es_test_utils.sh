#!/bin/bash
# Shared utilities for Elasticsearch eval tests
# Source this file at the start of before_test scripts:
#   source ../../shared/es_test_utils.sh

# Cluster shard limit - set high enough for all tests to run in parallel
# Total shards across all tests is ~3600, so 10000 gives plenty of headroom
ES_MAX_SHARDS_PER_NODE=10000

# Validate ES environment variables
es_validate_env() {
  if [ -z "$ELASTICSEARCH_URL" ]; then
    echo "❌ ELASTICSEARCH_URL environment variable is not set"
    exit 1
  fi

  if [ -z "$ELASTICSEARCH_API_KEY" ]; then
    echo "❌ ELASTICSEARCH_API_KEY environment variable is not set"
    exit 1
  fi
}

# Set cluster shard limit (idempotent - safe to call from multiple tests)
# By default, only verifies the limit is sufficient. Set ES_UPDATE_SHARD_LIMIT=true to update.
es_set_shard_limit() {
  local response
  local exit_code
  local current_limit

  # Get current shard limit
  response=$(curl -sf -X GET "${ELASTICSEARCH_URL}/_cluster/settings?include_defaults=true&flat_settings=true" \
    -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}" 2>&1)
  exit_code=$?

  if [ $exit_code -ne 0 ]; then
    echo "❌ Failed to get cluster settings (curl exit code: $exit_code)"
    echo "Response: $response"
    exit 1
  fi

  # Extract current limit (check persistent, then transient, then defaults)
  current_limit=$(echo "$response" | python3 -c "
import sys, json
d = json.load(sys.stdin)
limit = d.get('persistent', {}).get('cluster.max_shards_per_node')
if not limit:
    limit = d.get('transient', {}).get('cluster.max_shards_per_node')
if not limit:
    limit = d.get('defaults', {}).get('cluster.max_shards_per_node', '1000')
print(limit)
" 2>/dev/null)

  if [ -z "$current_limit" ]; then
    echo "❌ Could not determine current shard limit"
    echo "Response: $response"
    exit 1
  fi

  # Check if current limit is sufficient
  if [ "$current_limit" -ge "$ES_MAX_SHARDS_PER_NODE" ] 2>/dev/null; then
    echo "✅ Cluster shard limit ($current_limit) is sufficient (need $ES_MAX_SHARDS_PER_NODE)"
    return 0
  fi

  # Limit is too low - check if we're allowed to update
  if [ "$ES_UPDATE_SHARD_LIMIT" = "true" ]; then
    echo "⏳ Updating cluster shard limit from $current_limit to $ES_MAX_SHARDS_PER_NODE..."
    response=$(curl -sf -X PUT "${ELASTICSEARCH_URL}/_cluster/settings" \
      -H "Content-Type: application/json" \
      -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}" \
      -d "{\"persistent\": {\"cluster.max_shards_per_node\": ${ES_MAX_SHARDS_PER_NODE}}}" 2>&1)
    exit_code=$?

    if [ $exit_code -ne 0 ]; then
      echo "❌ Failed to set cluster shard limit (curl exit code: $exit_code)"
      echo "Response: $response"
      exit 1
    fi

    if ! echo "$response" | grep -q '"acknowledged":true'; then
      echo "❌ Cluster settings update not acknowledged"
      echo "Response: $response"
      exit 1
    fi
    echo "✅ Cluster shard limit updated to $ES_MAX_SHARDS_PER_NODE"
  else
    echo "❌ Cluster shard limit ($current_limit) is too low (need $ES_MAX_SHARDS_PER_NODE)"
    echo ""
    echo "To update the shard limit, rerun with:"
    echo "  ES_UPDATE_SHARD_LIMIT=true <command>"
    exit 1
  fi
}

# Combined setup: validate env + set shard limit
es_setup() {
  es_validate_env
  es_set_shard_limit
}

# Create a unique temp file for this test run
# Usage: BULK_FILE=$(es_temp_file "bulk" "186")
es_temp_file() {
  local prefix="${1:-es}"
  local test_id="${2:-$$}"
  local unique_id=$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 8 | head -n 1)
  echo "/tmp/${prefix}_${test_id}_${unique_id}.ndjson"
}

# Wait for index to be ready with retry loop
# Usage: es_wait_for_index "index-name" [max_attempts] [sleep_interval]
es_wait_for_index() {
  local index="$1"
  local max_attempts="${2:-30}"
  local sleep_interval="${3:-1}"

  echo "⏳ Waiting for index $index to be ready..."
  for i in $(seq 1 $max_attempts); do
    local response=$(curl -sf -X GET "${ELASTICSEARCH_URL}/_cat/indices/${index}?format=json" \
      -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}" 2>/dev/null)

    if [ -n "$response" ] && echo "$response" | grep -q "$index"; then
      echo "✅ Index $index is ready"
      return 0
    fi
    sleep $sleep_interval
  done

  echo "❌ Timeout waiting for index $index after $max_attempts attempts"
  return 1
}

# Wait for shards to be ready
# Usage: es_wait_for_shards "index-name" [expected_count] [max_attempts]
es_wait_for_shards() {
  local index="$1"
  local expected="${2:-1}"
  local max_attempts="${3:-30}"

  echo "⏳ Waiting for shards on $index..."
  for i in $(seq 1 $max_attempts); do
    local shard_info=$(curl -sf -X GET "${ELASTICSEARCH_URL}/_cat/shards/${index}?format=json" \
      -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}" 2>/dev/null)

    if [ -n "$shard_info" ]; then
      local count=$(echo "$shard_info" | grep -o '"prirep":"p"' | wc -l)
      if [ "$count" -ge "$expected" ]; then
        echo "✅ Found $count primary shards on $index"
        return 0
      fi
    fi
    sleep 1
  done

  echo "❌ Timeout waiting for shards on $index"
  return 1
}

# Validate expected shard count
# Usage: es_validate_shard_count "index-name" expected_count
es_validate_shard_count() {
  local index="$1"
  local expected="$2"

  local shard_info=$(curl -sf -X GET "${ELASTICSEARCH_URL}/_cat/shards/${index}?format=json" \
    -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}")

  local actual=$(echo "$shard_info" | grep -o '"prirep":"p"' | wc -l)

  if [ "$actual" != "$expected" ]; then
    echo "❌ Expected $expected primary shards on $index but found $actual"
    exit 1
  fi

  echo "✅ Verified $index has $actual primary shards"
}
