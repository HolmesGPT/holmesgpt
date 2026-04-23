# AWS Lambda High Latency Troubleshooting

## Overview
This runbook helps diagnose and resolve AWS Lambda function performance issues including high latency, timeouts, and cold starts.

## Steps

### Step 1: Query Performance Metrics
Use CloudWatch to check Lambda function latency over time:
```
aws cloudwatch get-metric-statistics --namespace <lambda-namespace> \
  --metric-name Duration --dimensions Name=FunctionName,Value=<function-name> \
  --statistics Average,Maximum --start-time <start-time> \
  --end-time <end-time> --period 300
```

### Step 2: Check Function Logs
Look for errors or slow operations in CloudWatch Logs:
```bash
aws logs filter-log-events --log-group-name <lambda-log-group>/<function-name> \
  --filter-pattern "[ERROR] OR [WARN] OR timeout" --limit 100
```

### Step 3: Analyze Cold Start Patterns
Query CloudWatch for cold start frequency:
```
aws cloudwatch get-metric-statistics --namespace <lambda-namespace> \
  --metric-name InitDuration --dimensions Name=FunctionName,Value=<function-name> \
  --statistics Average,Count --start-time <start-time> \
  --end-time <end-time> --period 300
```

### Step 4: Check Concurrent Executions
Verify Lambda isn't hitting concurrency limits:
```
aws cloudwatch get-metric-statistics --namespace <lambda-namespace> \
  --metric-name ConcurrentExecutions --dimensions Name=FunctionName,Value=<function-name> \
  --statistics Maximum --start-time <start-time> \
  --end-time <end-time> --period 60
```

### Step 5: Review Memory Utilization
Check if function needs more memory allocation:
```bash
aws logs insights start-query --log-group-name <lambda-log-group>/<function-name> \
  --start-time <epoch-start> --end-time <epoch-end> \
  --query-string "fields @timestamp, @message | filter @type = 'REPORT' | stats max(@maxMemoryUsed), avg(@memorySize)"
```
