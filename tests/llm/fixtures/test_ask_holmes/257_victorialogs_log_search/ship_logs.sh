#!/bin/sh
# Runs inside the cluster (curlimages/curl, busybox sh): ships a batch of
# payment-api logs to VictoriaLogs and verifies the ERROR lines are queryable.
set -eu

VL="http://victorialogs:9428"

READY=false
for i in $(seq 1 60); do
  if curl -sf "${VL}/health" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
done
if [ "$READY" = false ]; then
  echo "VictoriaLogs at ${VL} did not become healthy"
  exit 1
fi

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -sS --fail-with-body -X POST \
  "${VL}/insert/jsonline?_stream_fields=service,namespace&_msg_field=message&_time_field=time" \
  -H "Content-Type: application/stream+json" \
  --data-binary @- <<EOF
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"INFO","message":"Service started successfully"}
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"ERROR","message":"Database connection timeout - VLERR-7291: Failed to connect to primary database after 30s"}
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"WARN","message":"Retrying database connection"}
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"ERROR","message":"Payment validation failed - VLERR-4058: Invalid card number format"}
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"INFO","message":"Processing order batch complete"}
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"ERROR","message":"Stock sync failed - VLERR-9463: External inventory API returned 503"}
{"time":"$NOW","service":"payment-api-257","namespace":"app-257","level":"INFO","message":"Recovered from temporary outage"}
EOF

# Ingested rows become searchable after a short flush, so poll.
for i in $(seq 1 30); do
  RESULT=$(curl -sf "${VL}/select/logsql/query" \
    --data-urlencode 'query=_time:15m {service="payment-api-257",namespace="app-257"} level:ERROR' \
    -d 'limit=10' || true)
  if echo "$RESULT" | grep -q 'VLERR-7291' \
    && echo "$RESULT" | grep -q 'VLERR-4058' \
    && echo "$RESULT" | grep -q 'VLERR-9463'; then
    echo "Verified all 3 ERROR log lines are queryable (attempt $i)"
    exit 0
  fi
  sleep 1
done

echo "ERROR log lines not queryable after ingestion. Last query result:"
echo "$RESULT"
exit 1
