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
es_set_shard_limit() {
  curl -sf -X PUT "${ELASTICSEARCH_URL}/_cluster/settings" \
    -H "Content-Type: application/json" \
    -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}" \
    -d "{\"persistent\": {\"cluster.max_shards_per_node\": ${ES_MAX_SHARDS_PER_NODE}}}" > /dev/null 2>&1 || true
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
