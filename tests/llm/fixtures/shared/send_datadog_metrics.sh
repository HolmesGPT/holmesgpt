#!/bin/bash

# Shared script for sending CPU and memory metrics to Datadog
# Usage: bash send_datadog_metrics.sh <namespace> <pod_name> [deployment_name] [container_name]
#
# Example:
#   bash send_datadog_metrics.sh metrics-91b metrics-app
#   bash send_datadog_metrics.sh prod-91 app-91 app-91 app
#   bash send_datadog_metrics.sh deploy-91c $(kubectl get pods -n deploy-91c -l app=metrics-deployment -o jsonpath='{.items[0].metadata.name}') metrics-deployment

NAMESPACE="${1}"
POD_NAME="${2}"
DEPLOYMENT_NAME="${3:-}"
CONTAINER_NAME="${4:-app}"

if [ -z "$NAMESPACE" ] || [ -z "$POD_NAME" ]; then
    echo "ERROR: Usage: $0 <namespace> <pod_name> [deployment_name] [container_name]"
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

# Function to send metrics to Datadog
send_metrics() {
    local timestamp=$1
    local cpu_value=$2
    local memory_value=$3

    # Build tags array
    local tags="\"kube_namespace:${NAMESPACE}\",\"pod_name:${POD_NAME}\",\"kube_container_name:${CONTAINER_NAME}\",\"kube_cluster_name:production\",\"env:production\""

    # Add deployment tag if provided
    if [ -n "$DEPLOYMENT_NAME" ]; then
        tags="${tags},\"kube_deployment:${DEPLOYMENT_NAME}\""
    fi

    # Construct the metrics payload
    local payload=$(cat <<EOF
{
  "series": [
    {
      "metric": "container.cpu.usage",
      "points": [[$timestamp, $cpu_value]],
      "type": "gauge",
      "tags": [$tags]
    },
    {
      "metric": "container.memory.usage",
      "points": [[$timestamp, $memory_value]],
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
        echo "✓ Sent metrics for timestamp $timestamp (CPU: ${cpu_value}m, Memory: ${memory_value}MB)"
    else
        echo "✗ Failed to send metrics. Status: $status, Response: $body"
        exit 1
    fi
}

echo "Sending CPU and memory metrics to Datadog for pod ${POD_NAME} in namespace ${NAMESPACE}..."
echo "Using Datadog site: $DATADOG_SITE"

# Send metrics for the last 2 hours with varying CPU usage
# Normal baseline around 200-300 millicores with occasional spikes

# 2 hours ago - baseline
send_metrics $((CURRENT_TIME - 7200)) 250 512
send_metrics $((CURRENT_TIME - 6900)) 280 520
send_metrics $((CURRENT_TIME - 6600)) 265 515
send_metrics $((CURRENT_TIME - 6300)) 290 525

# 1.5 hours ago - small spike
send_metrics $((CURRENT_TIME - 5400)) 320 540
send_metrics $((CURRENT_TIME - 5100)) 450 580
send_metrics $((CURRENT_TIME - 4800)) 480 590
send_metrics $((CURRENT_TIME - 4500)) 350 550

# 1 hour ago - back to normal
send_metrics $((CURRENT_TIME - 3600)) 270 520
send_metrics $((CURRENT_TIME - 3300)) 285 525
send_metrics $((CURRENT_TIME - 3000)) 260 518
send_metrics $((CURRENT_TIME - 2700)) 295 530

# 45 minutes ago - larger spike
send_metrics $((CURRENT_TIME - 2400)) 550 620
send_metrics $((CURRENT_TIME - 2100)) 680 680
send_metrics $((CURRENT_TIME - 1800)) 720 700
send_metrics $((CURRENT_TIME - 1500)) 650 660

# Last 20 minutes - stabilizing
send_metrics $((CURRENT_TIME - 1200)) 420 580
send_metrics $((CURRENT_TIME - 900)) 380 560
send_metrics $((CURRENT_TIME - 600)) 320 540
send_metrics $((CURRENT_TIME - 300)) 290 530
send_metrics $((CURRENT_TIME - 60)) 275 525

echo ""
echo "Successfully sent CPU and memory metrics to Datadog"
echo "Pod: ${POD_NAME} in namespace ${NAMESPACE}"
if [ -n "$DEPLOYMENT_NAME" ]; then
    echo "Deployment: ${DEPLOYMENT_NAME}"
fi
echo "Metrics show varying CPU usage between 250-720 millicores with spikes"

# Wait a bit for metrics to be indexed in Datadog
sleep 15
