#!/bin/bash

# Script for sending custom cache hit ratio metrics to Datadog
# This sends the myapp.cache.hit.ratio metric with realistic values

NAMESPACE="cache-91e"
POD_NAME=$(kubectl get pods -n ${NAMESPACE} -l app=myapp -o jsonpath='{.items[0].metadata.name}')

if [ -z "$POD_NAME" ]; then
    echo "ERROR: Could not find myapp pod in namespace ${NAMESPACE}"
    exit 1
fi

# Check if required environment variables are set
if [ -z "$DATADOG_API_KEY" ]; then
    echo "ERROR: DATADOG_API_KEY environment variable is not set"
    exit 1
fi

# Use DATADOG_SITE or default to EU
DATADOG_SITE="${DATADOG_SITE:-https://api.datadoghq.eu}"

# Get current timestamp in seconds
CURRENT_TIME=$(date +%s)

# Function to send custom metrics to Datadog
send_cache_metrics() {
    local timestamp=$1
    local hit_ratio=$2

    # Build tags array
    local tags="\"kube_namespace:${NAMESPACE}\",\"pod_name:${POD_NAME}\",\"app:myapp\",\"kube_cluster_name:production\",\"env:production\""

    # Construct the metrics payload with custom metric
    local payload=$(cat <<EOF
{
  "series": [
    {
      "metric": "myapp.cache.hit.ratio",
      "points": [[$timestamp, $hit_ratio]],
      "type": "gauge",
      "tags": [$tags]
    }
  ]
}
EOF
)

    # Send to Datadog Metrics API (v1 endpoint)
    response=$(curl -X POST "${DATADOG_SITE}/api/v1/series" \
        -H "Content-Type: application/json" \
        -H "DD-API-KEY: ${DATADOG_API_KEY}" \
        -d "$payload" \
        --silent --write-out "HTTPSTATUS:%{http_code}")

    # Extract the body and status
    body=$(echo "$response" | sed -e 's/HTTPSTATUS:.*//g')
    status=$(echo "$response" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')

    if [ "$status" -eq 202 ] || [ "$status" -eq 200 ]; then
        echo "✓ Sent cache metrics for timestamp $timestamp (Hit Ratio: ${hit_ratio})"
    else
        echo "✗ Failed to send metrics. Status: $status, Response: $body"
        exit 1
    fi
}

echo "Sending custom cache hit ratio metrics to Datadog for pod ${POD_NAME} in namespace ${NAMESPACE}..."
echo "Using Datadog site: $DATADOG_SITE"

# Send realistic cache hit ratio metrics over the last 2 hours
# Cache typically starts cold and improves over time

# 2 hours ago - cold cache, low hit ratio
send_cache_metrics $((CURRENT_TIME - 7200)) 0.15
send_cache_metrics $((CURRENT_TIME - 6900)) 0.22
send_cache_metrics $((CURRENT_TIME - 6600)) 0.28
send_cache_metrics $((CURRENT_TIME - 6300)) 0.35

# 1.5 hours ago - warming up
send_cache_metrics $((CURRENT_TIME - 5400)) 0.42
send_cache_metrics $((CURRENT_TIME - 5100)) 0.48
send_cache_metrics $((CURRENT_TIME - 4800)) 0.55
send_cache_metrics $((CURRENT_TIME - 4500)) 0.58

# 1 hour ago - good performance
send_cache_metrics $((CURRENT_TIME - 3600)) 0.65
send_cache_metrics $((CURRENT_TIME - 3300)) 0.68
send_cache_metrics $((CURRENT_TIME - 3000)) 0.72
send_cache_metrics $((CURRENT_TIME - 2700)) 0.75

# 45 minutes ago - excellent performance
send_cache_metrics $((CURRENT_TIME - 2400)) 0.78
send_cache_metrics $((CURRENT_TIME - 2100)) 0.82
send_cache_metrics $((CURRENT_TIME - 1800)) 0.85
send_cache_metrics $((CURRENT_TIME - 1500)) 0.87

# Last 20 minutes - stable high performance with minor fluctuations
send_cache_metrics $((CURRENT_TIME - 1200)) 0.88
send_cache_metrics $((CURRENT_TIME - 900)) 0.86
send_cache_metrics $((CURRENT_TIME - 600)) 0.89
send_cache_metrics $((CURRENT_TIME - 300)) 0.91
send_cache_metrics $((CURRENT_TIME - 60)) 0.90

echo ""
echo "Successfully sent custom cache hit ratio metrics to Datadog"
echo "Pod: ${POD_NAME} in namespace ${NAMESPACE}"
echo "Metric: myapp.cache.hit.ratio shows improving cache performance from 15% to 90% hit ratio"

# Also send standard CPU and memory metrics using the shared script
echo ""
echo "Sending standard CPU and memory metrics..."
bash ../../shared/send_datadog_metrics.sh ${NAMESPACE} ${POD_NAME}

# Wait a bit for metrics to be indexed in Datadog
sleep 15
