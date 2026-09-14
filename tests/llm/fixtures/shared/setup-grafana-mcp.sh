#!/bin/bash
# Shared helper script for setting up Grafana MCP server in a test namespace.
# Assumes Grafana is already deployed and ready at grafana:3000 in the namespace.
#
# Usage: NS=app-177 source ../../shared/setup-grafana-mcp.sh
#
# Requires: NS environment variable set to the target namespace
set -e

if [ -z "$NS" ]; then
  echo "❌ NS environment variable must be set"
  exit 1
fi

echo "🔧 Setting up Grafana MCP server in namespace $NS..."

# Create a Grafana service account and token
echo "⏳ Creating Grafana service account..."
SA_RESPONSE=$(kubectl exec -n "$NS" deployment/grafana -- \
  wget -q -O- --post-data='{"name":"mcp-eval","role":"Admin"}' \
  --header="Content-Type: application/json" \
  http://localhost:3000/api/serviceaccounts 2>/dev/null) || {
  echo "❌ Failed to create service account"
  exit 1
}

SA_ID=$(echo "$SA_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])" 2>/dev/null) || {
  echo "❌ Failed to parse service account ID from response: $SA_RESPONSE"
  exit 1
}
echo "✅ Service account created (ID: $SA_ID)"

echo "⏳ Creating service account token..."
TOKEN_RESPONSE=$(kubectl exec -n "$NS" deployment/grafana -- \
  wget -q -O- --post-data='{"name":"mcp-token"}' \
  --header="Content-Type: application/json" \
  "http://localhost:3000/api/serviceaccounts/$SA_ID/tokens" 2>/dev/null) || {
  echo "❌ Failed to create service account token"
  exit 1
}

SA_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['key'])" 2>/dev/null) || {
  echo "❌ Failed to parse token from response: $TOKEN_RESPONSE"
  exit 1
}
echo "✅ Service account token created"

# Create the secret for the MCP server
kubectl create secret generic grafana-mcp-token \
  --from-literal=token="$SA_TOKEN" -n "$NS" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Deploy the MCP server
echo "⏳ Deploying Grafana MCP server..."
kubectl apply -f ../../shared/grafana-mcp.yaml -n "$NS"

# Wait for MCP server to be ready
echo "⏳ Waiting for Grafana MCP server to be ready..."
MCP_READY=false
for i in {1..60}; do
  if kubectl wait --for=condition=ready pod -l app=grafana-mcp -n "$NS" --timeout=5s 2>/dev/null; then
    echo "✅ Grafana MCP server is ready!"
    MCP_READY=true
    break
  fi
  echo "⏳ Attempt $i/60: MCP server not ready yet, waiting 1s..."
  sleep 1
done

if [ "$MCP_READY" = false ]; then
  echo "❌ Grafana MCP server failed to become ready after 60 seconds"
  kubectl get pods -n "$NS" -l app=grafana-mcp
  kubectl logs -n "$NS" -l app=grafana-mcp --tail=30
  exit 1
fi

echo "✅ Grafana MCP server setup complete in namespace $NS"
