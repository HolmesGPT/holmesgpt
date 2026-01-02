# Interactive Mode

HolmesGPT's interactive mode provides a powerful chat-like interface for iterative troubleshooting and investigation of cloud-native issues using LLMs.

## Getting Started

### Launching Interactive Mode

Interactive mode is the default when running `holmes ask`:

```bash
holmes ask
```

You can also provide an initial question:

```bash
holmes ask "what pods are failing?"
```

## Example Workflows

### Autonomous AI Investigation

In this example, the AI independently investigates and diagnoses an issue using its available tools:

```bash
holmes ask

> why is the payment-service in production namespace not responding to requests?

Running tool #1 kubectl_find_resource: kubectl get -A --show-labels -o wide deployment | grep payment
  Finished #1 in 1.32s, output length: 894 characters - /show 1 to view contents

Running tool #2 kubectl_describe: kubectl describe deployment payment-service -n production
  Finished #2 in 1.45s, output length: 2,341 characters - /show 2 to view contents

Running tool #3 kubectl_get_by_kind_in_namespace: kubectl get pod -n production -o wide --show-labels | grep -E 'NAME|payment'
  Finished #3 in 1.28s, output length: 456 characters - /show 3 to view contents

Running tool #4 fetch_pod_logs: Fetching logs for pod payment-service-7b9c5d4-xyz in namespace production
  Finished #4 in 2.87s, output length: 8,234 characters - /show 4 to view contents

Running tool #5 kubectl_events: kubectl get events -n production --field-selector involvedObject.name=payment-service-7b9c5d4-xyz
  Finished #5 in 1.19s, output length: 1,203 characters - /show 5 to view contents

Based on my investigation, the payment-service is not responding due to:

1. **Database Connection Failures**: The logs show repeated "connection refused" errors to `payment-db:5432`
2. **StatefulSet Down**: The payment-db StatefulSet has 0/1 replicas running
3. **PVC Pending**: The database's PersistentVolumeClaim is stuck in Pending status

Root cause appears to be storage-related. Let me check the PVC status.

> can you check why the PVC is pending?

Running tool #1 kubectl_describe: kubectl describe pvc payment-db-pvc-0 -n production
  Finished #1 in 1.38s, output length: 1,567 characters - /show 1 to view contents

Running tool #2 kubectl_get: kubectl get storageclass fast-ssd -o yaml
  Finished #2 in 1.14s, output length: 134 characters - /show 2 to view contents

Running tool #3 kubectl_get_by_kind_in_namespace: kubectl get storageclass
  Finished #3 in 1.23s, output length: 456 characters - /show 3 to view contents

The PVC is pending because the requested StorageClass doesn't exist:


  Error from server (NotFound): storageclasses.storage.k8s.io "fast-ssd" not found

Available StorageClasses in the cluster:
- **gp2** (default) - EBS General Purpose SSD
- **gp3** - EBS General Purpose SSD (newer generation)
- **io1** - EBS Provisioned IOPS SSD
- **standard** - EBS Magnetic volumes
```

### Human-in-the-Loop Investigation

In this example, the human provides additional context and runs commands the AI couldn't access:

```bash
holmes ask "users are reporting intermittent 502 errors on the checkout page but I don't see any pod failures"

Running tool #1 kubectl_get_by_kind_in_namespace: kubectl get pod -n production -o wide --show-labels
  Finished #1 in 1.41s, output length: 3,234 characters - /show 1 to view contents

Running tool #2 kubectl_top_pods: kubectl top pods -n production --containers
  Finished #2 in 2.18s, output length: 1,876 characters - /show 2 to view contents

Running tool #3 fetch_pod_logs: Fetching logs for pod frontend-6d4cf56-abc in namespace production. filter=502|error
  Finished #3 in 3.21s, output length: 456 characters - /show 3 to view contents

Running tool #4 prometheus_query: query='rate(http_requests_total{status="502"}[5m])'
  Finished #4 in 1.92s, output length: 234 characters - /show 4 to view contents

All pods are healthy with normal resource usage. The 502 errors in the logs show "upstream connect error". The errors spike every 10 minutes. I need access to:
- Load balancer/ingress controller logs
- Corporate proxy or WAF logs if you use one
- Database connection pool metrics from inside the app
- Any recent network or firewall changes

> /run aws elbv2 describe-target-health --target-group-arn arn:aws:elasticloadbalancing:us-east-1:123456:targetgroup/prod-tg/abc123
Running command: aws elbv2 describe-target-health --target-group-arn arn:aws:elasticloadbalancing:us-east-1:123456:targetgroup/prod-tg/abc123

{
  "TargetHealthDescriptions": [
    {
      "Target": {"Id": "i-0abc123", "Port": 80},
      "HealthCheckPort": "80",
      "TargetHealth": {
        "State": "unhealthy",
        "Reason": "Target.Timeout",
        "Description": "Request timed out"
      }
    }
  ]
}

Share output with AI? (Y/n): y
Add a comment or question (optional): This is our ALB target group

The issue is clearer now. The ALB target `i-0abc123` is failing health checks with timeouts. This EC2 instance appears to be outside your Kubernetes cluster.

> /run ssh admin@10.0.1.50 "sudo tail -100 /var/log/nginx/error.log | grep -i 'too many' | head -5"
Running command: ssh admin@10.0.1.50 "sudo tail -100 /var/log/nginx/error.log | grep -i 'too many' | head -5"

2024/11/20 10:00:01 [error] 1234#1234: *456789 limiting connections by zone "perip", client: 10.0.2.100
2024/11/20 10:10:02 [error] 1234#1234: *456790 limiting connections by zone "perip", client: 10.0.2.100
2024/11/20 10:20:01 [error] 1234#1234: *456791 limiting connections by zone "perip", client: 10.0.2.100
2024/11/20 10:30:03 [error] 1234#1234: *456792 limiting connections by zone "perip", client: 10.0.2.100
2024/11/20 10:40:02 [error] 1234#1234: *456793 limiting connections by zone "perip", client: 10.0.2.100

Share output with AI? (Y/n): y
Add a comment or question (optional): This is the nginx rate limiting on the EC2 instance. 10.0.2.100 is our k8s cluster NAT gateway

**Root Cause Found**: The 502 errors are caused by Nginx rate limiting on the EC2 instance:

1. **Every 10 minutes** your Kubernetes pods hit the rate limit
2. **Source IP problem**: All K8s traffic appears as one IP (10.0.2.100 - NAT Gateway)
3. **Nginx configuration**: `limit_req_zone $binary_remote_addr zone=perip` treats all K8s traffic as one client

**Solution**:
```nginx
# Update nginx config to use X-Forwarded-For for K8s traffic
map $remote_addr $limit_key {
    "10.0.2.100" $http_x_forwarded_for;  # K8s NAT Gateway
    default      $binary_remote_addr;
}
limit_req_zone $limit_key zone=perip:10m rate=100r/s;
```

### Scenarios to Consider Using /run or /shell

1. **Access to Data HolmesGPT Can't Access on It's Own**
   - ssh'ing into a machine
   - run commands with escalated permisssions (sudo)
   - accessing data sources HolmesGPT can't access on it's own

2. **Adding Business Context**
   - Pulling in marketing data with recent campaign launch times
   - Fetcing planned maintenance windows from a source HolmesGPT does not know about

3. **Sharing Development/Testing Insights**
   - Point HolmesGPT at recent code deployments or git commits


## Tips and Best Practices

1. **Use `/clear` when switching topics** - This gives you a fresh context and prevents confusion
3. **Use `/run` if the AI is missing something important** - Guide the investigation by showing it what it is missing
4. **Check `/context` periodically** - Especially during long investigations
5. **View evidence with `/show`** - Full outputs often contain important details
6. **Add comments when sharing shell output** - Helps the AI understand what you're looking for

## Beyond Interactive Mode

For additional HolmesGPT usage patterns, see:

- **[CI/CD Troubleshooting](cicd-troubleshooting.md)** - Integrate HolmesGPT into deployment pipelines
