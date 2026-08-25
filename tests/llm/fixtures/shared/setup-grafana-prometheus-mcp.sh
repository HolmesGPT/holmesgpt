#!/bin/bash
# Shared helper script for setting up Grafana + Prometheus datasource + MCP server
# in a test namespace that already has Prometheus deployed.
#
# This deploys Grafana, configures the local Prometheus as a datasource,
# and then sets up the Grafana MCP server.
#
# Usage: NS=app-124 PROM_URL=http://prometheus:9090 source ../../shared/setup-grafana-prometheus-mcp.sh
#
# Requires:
#   NS        - target namespace
#   PROM_URL  - in-cluster Prometheus URL (default: http://prometheus:9090)
set -e

if [ -z "$NS" ]; then
  echo "❌ NS environment variable must be set"
  exit 1
fi

PROM_URL="${PROM_URL:-http://prometheus:9090}"

echo "🔧 Setting up Grafana + Prometheus datasource + MCP server in namespace $NS..."

# Deploy Grafana
echo "⏳ Deploying Grafana..."
kubectl apply -f ../../shared/grafana.yaml -n "$NS"

# Wait for Grafana pod to be ready
echo "⏳ Waiting for Grafana pod to be ready..."
GRAFANA_READY=false
for i in {1..60}; do
  if kubectl wait --for=condition=ready pod -l app=grafana -n "$NS" --timeout=5s 2>/dev/null; then
    echo "✅ Grafana pod is ready!"
    GRAFANA_READY=true
    break
  fi
  echo "⏳ Attempt $i/60: Grafana pod not ready yet, waiting 1s..."
  sleep 1
done

if [ "$GRAFANA_READY" = false ]; then
  echo "❌ Grafana pod failed to become ready after 60 seconds"
  kubectl get pods -n "$NS" -l app=grafana
  kubectl logs -n "$NS" -l app=grafana --tail=20
  exit 1
fi

# Verify Grafana API is working
echo "⏳ Verifying Grafana API..."
API_READY=false
for i in {1..30}; do
  if kubectl exec -n "$NS" deployment/grafana -- wget -q -O- http://localhost:3000/api/health 2>/dev/null | grep -q "ok"; then
    echo "✅ Grafana API is working!"
    API_READY=true
    break
  fi
  echo "⏳ Attempt $i/30: Grafana API not ready yet, waiting 1s..."
  sleep 1
done

if [ "$API_READY" = false ]; then
  echo "❌ Grafana API failed verification"
  kubectl logs -n "$NS" -l app=grafana --tail=20
  exit 1
fi

# Add Prometheus as a datasource in Grafana
echo "⏳ Adding Prometheus datasource to Grafana..."
DS_PAYLOAD="{\"name\":\"Prometheus\",\"type\":\"prometheus\",\"url\":\"${PROM_URL}\",\"access\":\"proxy\",\"isDefault\":true}"
DS_CREATED=false
for i in {1..10}; do
  DS_RESPONSE=$(kubectl exec -n "$NS" deployment/grafana -- \
    wget -q -O- --post-data="$DS_PAYLOAD" \
    --header="Content-Type: application/json" \
    http://localhost:3000/api/datasources 2>/dev/null) || true

  if echo "$DS_RESPONSE" | grep -q '"id"'; then
    echo "✅ Prometheus datasource added!"
    DS_CREATED=true
    break
  elif echo "$DS_RESPONSE" | grep -q "already exists"; then
    echo "✅ Prometheus datasource already exists"
    DS_CREATED=true
    break
  else
    echo "⏳ Attempt $i/10: Failed to add datasource, waiting 1s... Response: $DS_RESPONSE"
    sleep 1
  fi
done

if [ "$DS_CREATED" = false ]; then
  echo "❌ Failed to add Prometheus datasource after 10 attempts"
  exit 1
fi

# Now set up the MCP server (reuse the grafana-mcp setup script)
NS="$NS" source ../../shared/setup-grafana-mcp.sh

echo "✅ Grafana + Prometheus datasource + MCP server setup complete in namespace $NS"
