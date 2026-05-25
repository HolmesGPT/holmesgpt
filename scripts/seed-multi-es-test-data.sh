#!/usr/bin/env bash
# Seed the two test Elasticsearch clusters with realistic data so every
# HolmesGPT Elasticsearch tool has something to return.
#
# Cluster A stays green: indices created with `number_of_replicas: 0`.
# Cluster B is forced yellow: indices created with `number_of_replicas: 1`
# on a single-node cluster, so the replica shards stay unassigned. This
# gives `elasticsearch_allocation_explain` a real explanation to return.
#
# Both clusters get the same indices (`app-logs-2026.05.25`,
# `metrics-2026.05.25`, `users`) so the LLM can do meaningful comparisons.
#
# Idempotent: each index is deleted before being recreated.
#
# Usage:
#   bash scripts/seed-multi-es-test-data.sh

set -euo pipefail

CONTEXT="${KUBECTL_CONTEXT:-avi-test-cluster2}"
NS_A="holmes-es-a"
NS_B="holmes-es-b"

PASSWORD_A=$(kubectl --context "$CONTEXT" -n "$NS_A" get secret elastic-credentials \
  -o jsonpath='{.data.ELASTIC_PASSWORD}' | base64 -d)
PASSWORD_B=$(kubectl --context "$CONTEXT" -n "$NS_B" get secret elastic-credentials \
  -o jsonpath='{.data.ELASTIC_PASSWORD}' | base64 -d)

# Run a curl from inside the cluster (via the ES pod itself) so we don't need
# port-forwards just to seed data.
es_curl() {
  local ns="$1"
  local password="$2"
  shift 2
  kubectl --context "$CONTEXT" -n "$ns" exec -i deploy/elasticsearch -- \
    curl -fsS -u "elastic:$password" -H 'Content-Type: application/json' "$@"
}

LOGS_INDEX="app-logs-2026.05.25"
METRICS_INDEX="metrics-2026.05.25"
USERS_INDEX="users"

create_index() {
  local ns="$1"
  local password="$2"
  local name="$3"
  local replicas="$4"
  local mappings="$5"

  echo "  • Creating index $name (replicas=$replicas) in $ns"
  es_curl "$ns" "$password" -X DELETE "http://localhost:9200/$name" >/dev/null 2>&1 || true
  es_curl "$ns" "$password" -X PUT "http://localhost:9200/$name" -d "$(cat <<JSON
{
  "settings": {
    "number_of_shards": 2,
    "number_of_replicas": $replicas
  },
  "mappings": $mappings
}
JSON
)" >/dev/null
}

bulk_insert() {
  local ns="$1"
  local password="$2"
  local payload="$3"
  echo "$payload" | kubectl --context "$CONTEXT" -n "$ns" exec -i deploy/elasticsearch -- \
    curl -fsS -u "elastic:$password" \
      -H 'Content-Type: application/x-ndjson' \
      -X POST "http://localhost:9200/_bulk" \
      --data-binary @- >/dev/null
}

# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

LOGS_MAPPINGS='{
  "properties": {
    "@timestamp": {"type": "date"},
    "level": {"type": "keyword"},
    "message": {"type": "text"},
    "service": {
      "properties": {
        "name": {"type": "keyword"},
        "version": {"type": "keyword"}
      }
    },
    "trace_id": {"type": "keyword"},
    "duration_ms": {"type": "long"}
  }
}'

METRICS_MAPPINGS='{
  "properties": {
    "@timestamp": {"type": "date"},
    "host": {"type": "keyword"},
    "metric_name": {"type": "keyword"},
    "value": {"type": "double"},
    "unit": {"type": "keyword"}
  }
}'

USERS_MAPPINGS='{
  "properties": {
    "id": {"type": "keyword"},
    "name": {"type": "text"},
    "email": {"type": "keyword"},
    "role": {"type": "keyword"},
    "active": {"type": "boolean"}
  }
}'

# ---------------------------------------------------------------------------
# Bulk payloads (same data in both clusters so the LLM can do comparisons)
# ---------------------------------------------------------------------------

logs_bulk() {
  cat <<'NDJSON'
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:01Z","level":"INFO","message":"Checkout completed for order 8821","service":{"name":"checkout-api","version":"2.4.1"},"trace_id":"trace-001","duration_ms":143}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:14Z","level":"INFO","message":"Payment captured via Stripe","service":{"name":"payment-service","version":"1.8.3"},"trace_id":"trace-001","duration_ms":312}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:01:02Z","level":"WARN","message":"Inventory lookup slower than threshold","service":{"name":"inventory-db","version":"3.0.0"},"trace_id":"trace-002","duration_ms":1450}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:01:37Z","level":"ERROR","message":"Connection refused to payment provider","service":{"name":"payment-service","version":"1.8.3"},"trace_id":"trace-003","duration_ms":5000}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:02:11Z","level":"INFO","message":"Retry succeeded after backoff","service":{"name":"payment-service","version":"1.8.3"},"trace_id":"trace-003","duration_ms":621}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:03:48Z","level":"ERROR","message":"Database deadlock detected; transaction rolled back","service":{"name":"inventory-db","version":"3.0.0"},"trace_id":"trace-004","duration_ms":210}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:05:00Z","level":"INFO","message":"Cart abandoned after 30 minutes idle","service":{"name":"checkout-api","version":"2.4.1"},"trace_id":"trace-005","duration_ms":12}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:07:22Z","level":"WARN","message":"Rate limiting applied to bot client","service":{"name":"checkout-api","version":"2.4.1"},"trace_id":"trace-006","duration_ms":3}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:09:11Z","level":"INFO","message":"Daily report generated","service":{"name":"inventory-db","version":"3.0.0"},"trace_id":"trace-007","duration_ms":880}
{"index":{"_index":"app-logs-2026.05.25"}}
{"@timestamp":"2026-05-25T09:10:55Z","level":"ERROR","message":"Out of memory in JSON parser","service":{"name":"checkout-api","version":"2.4.1"},"trace_id":"trace-008","duration_ms":18}
NDJSON
}

metrics_bulk() {
  cat <<'NDJSON'
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:00Z","host":"web-01","metric_name":"cpu_percent","value":42.5,"unit":"percent"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:00Z","host":"web-02","metric_name":"cpu_percent","value":38.1,"unit":"percent"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:00Z","host":"db-01","metric_name":"cpu_percent","value":71.8,"unit":"percent"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:00Z","host":"web-01","metric_name":"memory_percent","value":62.3,"unit":"percent"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:00Z","host":"web-02","metric_name":"memory_percent","value":58.0,"unit":"percent"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:00:00Z","host":"db-01","metric_name":"memory_percent","value":83.4,"unit":"percent"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:05:00Z","host":"db-01","metric_name":"disk_used_gb","value":412.6,"unit":"gigabytes"}
{"index":{"_index":"metrics-2026.05.25"}}
{"@timestamp":"2026-05-25T09:05:00Z","host":"web-01","metric_name":"disk_used_gb","value":58.2,"unit":"gigabytes"}
NDJSON
}

users_bulk() {
  cat <<'NDJSON'
{"index":{"_index":"users","_id":"u1"}}
{"id":"u1","name":"Alice Chen","email":"alice@example.com","role":"admin","active":true}
{"index":{"_index":"users","_id":"u2"}}
{"id":"u2","name":"Bob Singh","email":"bob@example.com","role":"developer","active":true}
{"index":{"_index":"users","_id":"u3"}}
{"id":"u3","name":"Carol Diaz","email":"carol@example.com","role":"viewer","active":false}
{"index":{"_index":"users","_id":"u4"}}
{"id":"u4","name":"Dan Becker","email":"dan@example.com","role":"developer","active":true}
{"index":{"_index":"users","_id":"u5"}}
{"id":"u5","name":"Eve Park","email":"eve@example.com","role":"viewer","active":true}
NDJSON
}

seed_cluster() {
  local ns="$1"
  local password="$2"
  local replicas="$3"
  echo "==> Seeding $ns (replicas=$replicas)"

  create_index "$ns" "$password" "$LOGS_INDEX"    "$replicas" "$LOGS_MAPPINGS"
  create_index "$ns" "$password" "$METRICS_INDEX" "$replicas" "$METRICS_MAPPINGS"
  create_index "$ns" "$password" "$USERS_INDEX"   "$replicas" "$USERS_MAPPINGS"

  echo "  • Bulk-inserting documents"
  bulk_insert "$ns" "$password" "$(logs_bulk)"
  bulk_insert "$ns" "$password" "$(metrics_bulk)"
  bulk_insert "$ns" "$password" "$(users_bulk)"

  echo "  • Forcing refresh"
  es_curl "$ns" "$password" -X POST "http://localhost:9200/_refresh" >/dev/null
}

# cluster-a → green (replicas=0)
seed_cluster "$NS_A" "$PASSWORD_A" 0
# cluster-b → yellow (replicas=1 on a single-node cluster)
seed_cluster "$NS_B" "$PASSWORD_B" 1

echo
echo "==> Final cluster health"
for entry in "$NS_A:$PASSWORD_A:cluster-a:green" "$NS_B:$PASSWORD_B:cluster-b:yellow"; do
  IFS=: read -r ns pw name expected <<<"$entry"
  status=$(es_curl "$ns" "$pw" "http://localhost:9200/_cluster/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  if [[ "$status" == "$expected" ]]; then
    echo "✅ $name: $status (expected $expected)"
  else
    echo "⚠️  $name: $status (expected $expected)"
  fi
done

echo
echo "✅ Seed complete. Both clusters have indices: $LOGS_INDEX, $METRICS_INDEX, $USERS_INDEX"
