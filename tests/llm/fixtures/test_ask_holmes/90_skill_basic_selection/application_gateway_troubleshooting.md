# Application Gateway Troubleshooting

## Overview
This runbook helps troubleshoot common Application Gateway issues including 502 Bad Gateway errors.

## Prerequisites
- kubectl access to the cluster
- Ability to view pod logs

## Troubleshooting Steps

### Step 1: Find the Application Gateway Pod Status
Use kubectl to check if the Application Gateway pod is running:
```bash
kubectl get pods -n <namespace> | grep application-gateway
```

### Step 2: Check Application Gateway error Logs
Review the Application Gateway error logs for any issues:
```bash
kubectl logs <application-gateway-pod> -n <namespace> --tail=100 | grep -i error
```

### Step 3: Verify Upstream Services
Check if the upstream services that Application Gateway proxies to are healthy:
```bash
kubectl get pods -n <application-gateway-pod> -l app=upstream-service
```

### Step 4: Check Application Gateway Configuration
Verify the Application Gateway configuration is valid:
```bash
kubectl exec <application-gateway-pod> -n <namespace> -- appg -t
```

### Step 5: Review Service Endpoints
Ensure the Application Gateway has healthy endpoints:
```bash
kubectl get endpoints -n <namespace>
```

## Resolution Steps
Based on findings:
- If upstream is down: Restart upstream services
- If config invalid: Fix configuration and reload
- If no endpoints: Check service selectors
