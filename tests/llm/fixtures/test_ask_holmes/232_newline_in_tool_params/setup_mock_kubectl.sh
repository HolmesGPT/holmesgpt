#!/bin/bash
set -e

REAL_KUBECTL=$(which kubectl)
MOCK_DIR=/tmp/eval-232-mock-bin
FLEET_JSON=/tmp/eval-232-fleet.json

mkdir -p "$MOCK_DIR"

# Write fleet JSON data
cat > "$FLEET_JSON" << 'JSON_DATA'
{
  "apiVersion": "v1",
  "kind": "ConfigMapList",
  "metadata": {"resourceVersion": "12345"},
  "items": [
    {
      "metadata": {"name": "fleet-inventory", "namespace": "monitoring"},
      "data": {
        "fleet": {
          "cluster_id": "prod-us-east-1",
          "services": [
            {
              "name": "payment-gateway",
              "region": "us-east-1",
              "instances": [
                {"id": "pg-001", "status": "healthy", "health_check": {"timestamp": "2024-06-01T10:00:00Z", "trace_id": "EVAL-232-a1b2c3d4", "latency_ms": 12}},
                {"id": "pg-002", "status": "unhealthy", "health_check": {"timestamp": "2024-06-01T10:05:00Z", "trace_id": "EVAL-232-f7k9x2m4", "latency_ms": 5200}}
              ]
            },
            {
              "name": "order-processor",
              "region": "us-east-1",
              "instances": [
                {"id": "op-001", "status": "healthy", "health_check": {"timestamp": "2024-06-01T10:00:00Z", "trace_id": "EVAL-232-e5f6g7h8", "latency_ms": 8}},
                {"id": "op-002", "status": "healthy", "health_check": {"timestamp": "2024-06-01T10:05:00Z", "trace_id": "EVAL-232-i9j0k1l2", "latency_ms": 15}}
              ]
            },
            {
              "name": "session-store",
              "region": "eu-west-1",
              "instances": [
                {"id": "ss-001", "status": "unhealthy", "health_check": {"timestamp": "2024-06-01T09:55:00Z", "trace_id": "EVAL-232-m3n4o5p6", "latency_ms": 9800}},
                {"id": "ss-002", "status": "healthy", "health_check": {"timestamp": "2024-06-01T10:00:00Z", "trace_id": "EVAL-232-q7r8s9t0", "latency_ms": 22}}
              ]
            }
          ]
        }
      }
    }
  ]
}
JSON_DATA

# Write mock kubectl script
cat > "$MOCK_DIR/kubectl" << EOF
#!/bin/bash
# Mock kubectl for eval 232

# Handle "api-resources"
if echo "\$*" | grep -q "api-resources"; then
  echo "configmaps                        cm           v1                                     true         ConfigMap"
  exit 0
fi

# Handle "get --raw /api/v1/configmaps..." (kubernetes_jq_query pagination path)
if echo "\$*" | grep -q -- "--raw.*/api/v1/configmaps"; then
  cat "$FLEET_JSON"
  exit 0
fi

# Handle "get configmaps"
if echo "\$*" | grep -q "get.*configmap"; then
  cat "$FLEET_JSON"
  exit 0
fi

# Pass through to real kubectl
exec $REAL_KUBECTL "\$@"
EOF

chmod +x "$MOCK_DIR/kubectl"

# Verify mock works
export PATH="$MOCK_DIR:$PATH"

API_INFO=$(kubectl api-resources --no-headers 2>/dev/null | grep "^configmaps " | head -1)
if [ -z "$API_INFO" ]; then
  echo "❌ Mock kubectl api-resources failed"
  exit 1
fi

RAW_OUTPUT=$(kubectl get --raw "/api/v1/configmaps?limit=500" 2>/dev/null)
if echo "$RAW_OUTPUT" | jq -e '.items | length > 0' > /dev/null 2>&1; then
  echo "✅ Mock kubectl is working (api-resources + get --raw)"
else
  echo "❌ Mock kubectl get --raw failed"
  exit 1
fi
