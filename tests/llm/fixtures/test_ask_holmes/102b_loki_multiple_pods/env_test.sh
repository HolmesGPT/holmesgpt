
# Get the pod names
POD_NAMES=$(kubectl get pods -n app-102b -l app=nginx -o jsonpath='{.items[*].metadata.name}')
echo "Found pods: $POD_NAMES"

# Convert to array
read -ra PODS <<< "$POD_NAMES"

# Push 50 success logs for each pod
LOKI_URL="http://localhost:3100/loki/api/v1/push"
TIMESTAMP_NS=$(date +%s)000000000

for POD in "${PODS[@]}"; do
echo "Pushing 50 success logs for pod: $POD"

# Build log entries for this pod
LOG_ENTRIES=""
for i in $(seq 1 50); do
    # Increment timestamp for each log
    TS=$((TIMESTAMP_NS + i * 1000000))
    STATUS_CODES=(200 200 200 200 201 204)
    STATUS=${STATUS_CODES[$((RANDOM % ${#STATUS_CODES[@]}))]}
    ENDPOINTS=("/api/v1/users" "/api/v1/products" "/api/v1/orders" "/api/v1/inventory" "/health")
    ENDPOINT=${ENDPOINTS[$((RANDOM % ${#ENDPOINTS[@]}))]}
    RESPONSE_TIME=$((50 + RANDOM % 150))

    LOG_LINE="{\\\"level\\\":\\\"INFO\\\",\\\"message\\\":\\\"HTTP request completed\\\",\\\"method\\\":\\\"GET\\\",\\\"path\\\":\\\"${ENDPOINT}\\\",\\\"status\\\":${STATUS},\\\"response_time_ms\\\":${RESPONSE_TIME},\\\"client_ip\\\":\\\"10.0.$((RANDOM % 256)).$((RANDOM % 256))\\\"}"

    if [ -n "$LOG_ENTRIES" ]; then
    LOG_ENTRIES="${LOG_ENTRIES},[\"${TS}\",\"${LOG_LINE}\"]"
    else
    LOG_ENTRIES="[\"${TS}\",\"${LOG_LINE}\"]"
    fi
done

# Push logs to Loki
PAYLOAD="{\"streams\":[{\"stream\":{\"job\":\"nginx\",\"namespace\":\"app-102b\",\"pod\":\"${POD}\",\"app\":\"nginx\"},\"values\":[${LOG_ENTRIES}]}]}"

curl -s -X POST "${LOKI_URL}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}" && echo "✅ Pushed 50 logs for ${POD}"
done


# Push 1 error log for the first pod only
ERROR_POD="${PODS[0]}"
echo "Pushing error log for pod: $ERROR_POD"

# Calculate timestamp (current time in nanoseconds)
ERROR_TS="$(date +%s)000000000"

curl -s -X POST 'http://localhost:3100/loki/api/v1/push' \
-H 'Content-Type: application/json' \
-d "{
    \"streams\": [
    {
        \"stream\": {
        \"job\": \"nginx\",
        \"namespace\": \"app-102b\",
        \"pod\": \"${ERROR_POD}\",
        \"container\": \"nginx\",
        \"app\": \"nginx\",
        \"level\": \"ERROR\"
        },
        \"values\": [
        [\"${ERROR_TS}\", \"{\\\"level\\\":\\\"ERROR\\\",\\\"message\\\":\\\"Memory alert\\\",\\\"error\\\":\\\"Memory requests above 90%, consider adding more memory\\\",\\\"status\\\":501,\\\"request_id\\\":\\\"req-a]8f2c1d4\\\"}\"]
        ]
    }
    ]
}" && echo "✅ Pushed error log for ${ERROR_POD}"

echo "Log injection complete!"
