# Database Connection Troubleshooting

## Overview
This runbook helps diagnose database connection timeout issues in Kubernetes applications.

## Investigation Steps

### 1. Check Application Status
Verify the application pods are running and review recent events:
```bash
kubectl get pods -n {namespace} -l app={app_name}
kubectl describe pod -n {namespace} -l app={app_name}
```

### 2. Review Application Logs
Look for connection errors and timeout patterns:
```bash
kubectl logs -n {namespace} -l app={app_name} --tail=50 | grep -E "(timeout|connection|ERROR)"
```

### 3. Verify Database Service
Check if the database service exists and has endpoints:
```bash
kubectl get svc -n {namespace} | grep -E "(postgres|mysql|database)"
kubectl get endpoints -n {namespace} | grep -E "(postgres|mysql|database)"
```

### 4. Test Network Connectivity
Test connectivity from application pod to database:
```bash
kubectl exec -n {namespace} $(kubectl get pod -n {namespace} -l app={app_name} -o jsonpath="{.items[0].metadata.name}") -- nc -zv {db_host} {db_port}
```

### 5. Check Database Health
Connect to database and check its status:
```bash
# For PostgreSQL
kubectl exec -n {namespace} {db_pod} -- psql -U postgres -c "SELECT 1"

# Check active connections
kubectl exec -n {namespace} {db_pod} -- psql -U postgres -c "SELECT count(*) FROM pg_stat_activity"
```

### 6. Review Connection Pool Configuration
Check application's database connection pool settings:
```bash
kubectl get deployment -n {namespace} {app_name} -o yaml | grep -A10 "env:"
```

## Common Issues

1. **No Database Endpoints**: Database service has no backing pods
2. **Connection Pool Exhaustion**: All connections in use, increase pool size
3. **Network Policy**: Check if NetworkPolicies block database access
4. **DNS Resolution**: Verify database hostname resolves correctly
5. **Database Overload**: Too many connections, database not responding
