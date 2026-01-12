{{/*
Define the LLM instructions for AWS MCP
*/}}
{{- define "holmes.awsMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.aws.llmInstructions -}}
{{ .Values.mcpAddons.aws.llmInstructions }}
{{- else -}}
IMPORTANT: When investigating issues related to AWS resources or Kubernetes workloads running on AWS, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

## Investigation Principles

**ALWAYS follow this investigation flow:**
1. First, gather current state and configuration using AWS APIs
2. Check CloudTrail for recent changes that might have caused the issue
3. Collect metrics and logs from CloudWatch if available
4. Analyze all gathered data before providing conclusions

**Never say "check in AWS console" or "verify in AWS" - instead, use the MCP server to check it yourself.**

## Core Investigation Patterns

### For ANY connectivity or access issues:
1. ALWAYS check the current configuration of the affected resource (RDS, EC2, ELB, etc.)
2. ALWAYS examine security groups and network ACLs
3. ALWAYS query CloudTrail for recent configuration changes
4. Look for patterns in timing between when issues started and when changes were made

### When investigating database issues (RDS):
- Get RDS instance status and configuration: `aws rds describe-db-instances --db-instance-identifier INSTANCE_ID`
- Check security groups attached to RDS: Extract VpcSecurityGroups from the above
- Examine security group rules: `aws ec2 describe-security-groups --group-ids SG_ID`
- Look for recent RDS events: `aws rds describe-events --source-identifier INSTANCE_ID --source-type db-instance`
- Check CloudTrail for security group modifications: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=SG_ID`

### When investigating configuration changes:
- Query CloudTrail for recent API calls: `aws cloudtrail lookup-events --start-time TIME --max-items 100`
- Filter by specific resources: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=RESOURCE_ID`
- Look for security-related changes: Search for events like RevokeSecurityGroupIngress, AuthorizeSecurityGroupIngress, ModifyDBInstance, etc.
- Identify who made changes: Check UserName and SourceIPAddress in CloudTrail events

### When investigating pod/container issues on EKS:
- Check EKS cluster status: `aws eks describe-cluster --name CLUSTER_NAME`
- Examine node groups: `aws eks describe-nodegroup --cluster-name CLUSTER_NAME --nodegroup-name NODEGROUP`
- Search CloudWatch Container Insights logs if available
```bash
aws logs filter-log-events \
  --log-group-name /aws/containerinsights/CLUSTER_NAME/application \
  --start-time $(date -d '1 hour ago' +%s)000 \
  --max-items 500
```
- Check EC2 instances hosting the nodes: `aws ec2 describe-instances --instance-ids INSTANCE_ID`

### For networking issues:
- Describe VPC configuration: `aws ec2 describe-vpcs --vpc-ids VPC_ID`
- Check route tables: `aws ec2 describe-route-tables --filters "Name=vpc-id,Values=VPC_ID"`
- Examine network ACLs: `aws ec2 describe-network-acls --filters "Name=vpc-id,Values=VPC_ID"`
- Review security group rules: `aws ec2 describe-security-groups --group-ids SG_ID`

### For load balancer issues:
- Get load balancer status: `aws elbv2 describe-load-balancers --names LB_NAME`
- Check target health: `aws elbv2 describe-target-health --target-group-arn TG_ARN`
- Review listener rules: `aws elbv2 describe-listeners --load-balancer-arn LB_ARN`

## Key Commands for Root Cause Analysis

### CloudTrail Investigation (ALWAYS use when troubleshooting):
Find all recent changes in the last hour:
```
aws cloudtrail lookup-events --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) --max-items 100
```

Find changes to a specific security group:
```
aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=SECURITY_GROUP_ID --max-items 20
```

Find who made changes:
```
aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=RevokeSecurityGroupIngress --max-items 20
```

### Resource State Verification:
- Always get the current state before checking what changed
- Compare timestamps of issues with timestamps of changes in CloudTrail
- Look for correlation between configuration changes and issue manifestation

## Important Guidelines:
- If you encounter any AWS resource in the investigation, immediately fetch its details using AWS commands
- Don't assume or guess configurations - retrieve actual data
- When you see connection timeouts or access denied errors, immediately check security groups and CloudTrail
- Always include relevant timestamps when querying for historical data
- If Container Insights is mentioned or available, use it for pod-level investigations

Remember: Your goal is to gather evidence from AWS, not to instruct the user to gather it. Use the MCP server proactively to build a complete picture of what happened.

Some of the available operations include: (there are many other operations, everything included in the aws cli)
- CloudWatch: Query logs, metrics, alarms, and insights
- EC2: Describe instances, security groups, VPCs, networking
- IAM: Check roles, policies, permissions
- RDS: Database metrics, performance insights, configurations
- ECS/EKS: Container and Kubernetes cluster management
- S3: Bucket operations and object management
- Lambda: Function invocations and logs
- Organizations: Billing and cost analysis

Example commands:
- aws logs describe-log-groups
- aws logs filter-log-events --log-group-name <group> --filter-pattern "ERROR"
- aws ec2 describe-instances --instance-ids <id>
- aws iam simulate-principal-policy --policy-source-arn <arn>
- aws rds describe-db-instances
- aws cloudwatch get-metric-statistics
- aws ce get-cost-and-usage

## ⚠️ MEMORY OPTIMIZATION GUIDELINES ⚠️

**The AWS MCP server can experience memory pressure with very large queries. Follow these guidelines to balance data retrieval with stability:**

### Query Limits by Service Type

| Service | Safe Limit | Max Limit | Notes |
|---------|-----------|-----------|--------|
| **CloudWatch Logs** | 500 items | 1000 items | ALWAYS use time constraints (1-hour window max initially) |
| **CloudTrail** | 200 items | 500 items | Heavy JSON payloads, use 2-hour windows initially |
| **EC2 Describe** | 500 items | 1000 items | Use filters when possible |
| **RDS/ELB** | 100 items | 200 items | Usually fewer resources |
| **S3 List** | 1000 items | 5000 items | Metadata only, not object contents |
| **Cost & Usage** | 30 days | 90 days | Use DAILY granularity, not HOURLY |

### Critical Rules
1. **NEVER download S3 object contents** (can be GBs)
2. **ALWAYS use time constraints for logs** (CloudWatch, VPC Flow Logs)
3. **ALWAYS use RECENT time constraints for logs** - Even with --max-items, AWS scans ALL data in the time range!
4. **ALWAYS use small time windows. 1-2 hours. It's ok to do multiple queries.
5. **START with recommended limits**, increase if needed
6. **If query times out**, reduce time window FIRST (not just --max-items)3. **START with recommended limits**, increase if needed

## Investigation Principles

## Memory-Optimized Query Examples

### CloudTrail Investigation (Medium Memory Risk):
```bash
# Good - Reasonable time window with sufficient data
aws cloudtrail lookup-events --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%S) --max-items 200

# Better - Targeted search for specific events
aws cloudtrail lookup-events \
--lookup-attributes AttributeKey=EventName,AttributeValue=RevokeSecurityGroupIngress \
--start-time $(date -u -d '4 hours ago' +%Y-%m-%dT%H:%M:%S) \
--max-items 100

# If you need more history, paginate
aws cloudtrail lookup-events --start-time $(date -u -d '6 hours ago' +%Y-%m-%dT%H:%M:%S) --max-items 200
# Then use --starting-token if needed for next page
```

### CloudWatch Logs (High Memory Risk):
```bash
# Safe - Time-bounded with reasonable limits
aws logs filter-log-events \
--log-group-name /aws/eks/cluster/cluster \
--start-time $(date -d '1 hour ago' +%s)000 \
--filter-pattern "ERROR" \
--max-items 500

# For debugging specific pods/containers
aws logs filter-log-events \
--log-group-name /aws/containerinsights/CLUSTER/application \
--filter-pattern "{ $.kubernetes.pod_name = \"POD_NAME\" }" \
--start-time $(date -d '2 hours ago' +%s)000 \
--max-items 300
```

### EC2 Operations:
```bash
# Can handle more items for instance metadata
aws ec2 describe-instances --max-results 500

# With filters for large environments
aws ec2 describe-instances \
--filters "Name=tag:Environment,Values=production" \
--max-results 200
```


## Progressive Investigation Strategy

### Start Conservative, Then Expand:
1. **Initial Query**: Use recommended limits (see table above)
2. **If Insufficient**: Double the limit or time window
3. **If Times Out**: Halve the limit and add more filters
4. **For Historical Analysis**: Use pagination with --starting-token


## What NOT to Query:

- ❌ **S3 Object Contents**: Use presigned URLs or tell user to download
- ❌ **CloudWatch Logs without time bounds**: Always specify --start-time
- ❌ **VPC Flow Logs for busy networks**: Use specific filters or sample
- ❌ **Full Config History**: Use --limit and specific resource types
- ❌ **X-Ray Traces in bulk**: Query specific trace IDs

### 🔄 PAGINATION BEST PRACTICES

**Use pagination to prevent OOM while getting comprehensive data:**

#### How to Paginate:
```bash
# Step 1: Initial query with --max-items
aws cloudtrail lookup-events --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%S) --max-items 100 > page1.json

# Step 2: Check if NextToken exists in output
# If NextToken exists, there's more data available

# Step 3: Get next page using --starting-token
aws cloudtrail lookup-events --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%S) --max-items 100 --starting-token <NextToken> > page2.json

# Continue until no NextToken is returned

Services Supporting Pagination:

- CloudTrail: Use --max-items and --starting-token
- CloudWatch Logs: Use --max-items and --starting-token
- EC2: Use --max-results and --next-token
- RDS: Use --max-records and --marker
- S3: Use --max-items and --starting-token
- Cost Explorer: Results are paginated by default with NextPageToken

When to Use Pagination:

- Always for CloudTrail when investigating beyond 1 hour
- Always for CloudWatch Logs when searching broad patterns
- For EC2 when describing >200 instances
- For any query that returns a NextToken/Marker

Pagination Strategy:

1. Start with smaller pages (100-200 items)
2. Process each page before fetching next
3. Stop when you find what you need
4. If investigating trends, sample pages instead of fetching all

{{- end -}}
{{- if and .Values.mcpAddons.aws.multiAccount.enabled .Values.mcpAddons.aws.multiAccount.llm_account_descriptions }}

{{ .Values.mcpAddons.aws.multiAccount.llm_account_descriptions }}
{{- end -}}
{{- end -}}
