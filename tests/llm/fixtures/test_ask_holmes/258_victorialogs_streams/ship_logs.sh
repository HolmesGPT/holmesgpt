#!/bin/sh
# Runs inside the cluster (curlimages/curl, busybox sh): ships a few log lines
# per service to VictoriaLogs and verifies each service's stream is queryable.
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

# Each service becomes its own stream.
curl -sf -X POST \
  "${VL}/insert/jsonline?_stream_fields=service,namespace&_msg_field=message&_time_field=time" \
  -H "Content-Type: application/stream+json" \
  --data-binary @- <<EOF
{"time":"$NOW","service":"checkout-258","namespace":"app-258","message":"Cart updated"}
{"time":"$NOW","service":"checkout-258","namespace":"app-258","message":"Order placed"}
{"time":"$NOW","service":"inventory-258","namespace":"app-258","message":"Stock decremented"}
{"time":"$NOW","service":"inventory-258","namespace":"app-258","message":"Low stock warning"}
{"time":"$NOW","service":"shipping-258","namespace":"app-258","message":"Label printed"}
{"time":"$NOW","service":"shipping-258","namespace":"app-258","message":"Tracking number issued"}
EOF

# Ingested rows become searchable after a short flush, so poll.
for i in $(seq 1 30); do
  RESULT=$(curl -sf "${VL}/select/logsql/streams" \
    --data-urlencode 'query=_time:15m {namespace="app-258"}' || true)
  if echo "$RESULT" | grep -q 'checkout-258' \
    && echo "$RESULT" | grep -q 'inventory-258' \
    && echo "$RESULT" | grep -q 'shipping-258'; then
    echo "Verified all 3 service streams are queryable in app-258 (attempt $i)"
    exit 0
  fi
  sleep 1
done

echo "Service streams not queryable after ingestion. Last query result:"
echo "$RESULT"
exit 1
