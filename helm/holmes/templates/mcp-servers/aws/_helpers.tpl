{{/*
Default LLM instructions for the hosted AWS MCP Server
*/}}
{{- define "holmes.awsMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.aws.llmInstructions -}}
{{ .Values.mcpAddons.aws.llmInstructions }}
{{- else -}}
IMPORTANT: When investigating issues related to AWS resources or Kubernetes workloads running on AWS, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

## How to call AWS APIs

- `aws___run_script` runs Python in a sandbox where `call_boto3` is **async and keyword-only**: `data = await call_boto3(service_name='rds', operation_name='DescribeDBInstances', params={'DBInstanceIdentifier': ID}, region_name='us-east-1')`. Operation names are PascalCase (`DescribeDBInstances`, not `describe-db-instances`); `region_name` is optional. Build a `result = {...}` dict and end the script with `result` as the final expression. Each call takes 10-20 seconds, so batch related lookups into one script and return only the fields you need.
- `aws___search_documentation` / `aws___read_documentation` when you are unsure of an operation name or its parameters.
- `aws___list_regions` and `aws___get_regional_availability` for region questions.
- Each configured MCP server is one AWS account; pick the server matching the account you are investigating.

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
- `await call_boto3(service_name='rds', operation_name='DescribeDBInstances', params={'DBInstanceIdentifier': ID})` then read `VpcSecurityGroups`
- `await call_boto3(service_name='ec2', operation_name='DescribeSecurityGroups', params={'GroupIds': [SG_ID]})`
- `await call_boto3(service_name='rds', operation_name='DescribeEvents', params={'SourceIdentifier': ID, 'SourceType': 'db-instance'})`
- `await call_boto3(service_name='cloudtrail', operation_name='LookupEvents', params={'LookupAttributes': [{'AttributeKey': 'ResourceName', 'AttributeValue': SG_ID}]})`

### When investigating configuration changes:
- `await call_boto3(service_name='cloudtrail', operation_name='LookupEvents', params={'StartTime': ..., 'MaxResults': 50})`; filter by `ResourceName` or `EventName` (RevokeSecurityGroupIngress, AuthorizeSecurityGroupIngress, ModifyDBInstance, ...)
- Identify who made changes from `Username` and `sourceIPAddress` in the event JSON

### When investigating pod/container issues on EKS:
- `eks`: `DescribeCluster` (`params={'name': ...}`) and `DescribeNodegroup` (`params={'clusterName': ..., 'nodegroupName': ...}`)
- `logs`: `FilterLogEvents` with `params={'logGroupName': '/aws/containerinsights/CLUSTER/application', 'startTime': ..., 'limit': 200}` when Container Insights is enabled
- `ec2`: `DescribeInstances` with `params={'InstanceIds': [...]}` for the nodes

### For networking and load balancer issues:
- `ec2`: `DescribeVpcs`, `DescribeRouteTables`, `DescribeNetworkAcls`, `DescribeSecurityGroups`
- `elbv2`: `DescribeLoadBalancers`, `DescribeTargetHealth`, `DescribeListeners`

## Keep results small

- ALWAYS bound log and CloudTrail queries by time (start with 1-2 hour windows) and by `limit`/`MaxResults`; paginate with `NextToken` instead of requesting everything
- NEVER download S3 object contents
- Use DAILY granularity for Cost Explorer; prefer `GroupBy` over complex filters
- Summarize in the script (counts, the matching rows) into `result` instead of returning whole responses

Remember: Your goal is to gather evidence from AWS, not to instruct the user to gather it. Use the MCP server proactively to build a complete picture of what happened.
{{- end -}}
{{- if and .Values.mcpAddons.aws.multiAccount.enabled .Values.mcpAddons.aws.multiAccount.llm_account_descriptions }}

{{ .Values.mcpAddons.aws.multiAccount.llm_account_descriptions }}
{{- end -}}
{{- end -}}

{{/*
True when Holmes should sign with per-account profiles from the rendered AWS config file
*/}}
{{- define "holmes.awsMcp.multiAccount" -}}
{{- if and .Values.mcpAddons.aws.enabled .Values.mcpAddons.aws.multiAccount.enabled .Values.mcpAddons.aws.multiAccount.profiles -}}true{{- end -}}
{{- end -}}

{{/*
mcp_servers entries for the hosted AWS MCP Server: one per account
*/}}
{{- define "holmes.awsMcp.servers" -}}
{{- $servers := dict -}}
{{- $base := dict "mode" "aws" "icon_url" "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/aws.svg" -}}
{{- $instructions := include "holmes.awsMcp.llmInstructions" . | trim -}}
{{- if include "holmes.awsMcp.multiAccount" . -}}
{{- range $profile, $account := .Values.mcpAddons.aws.multiAccount.profiles -}}
{{- $config := merge (dict "profile" $profile "region" ($account.region | default $.Values.mcpAddons.aws.config.region)) $base -}}
{{- $_ := set $servers (printf "aws_%s" $profile) (dict
    "description" ($account.description | default (printf "AWS MCP Server for the %s account (%s)" $profile $account.account_id))
    "config" $config
    "llm_instructions" $instructions) -}}
{{- end -}}
{{- else -}}
{{- $_ := set $servers "aws_api" (dict
    "description" "AWS MCP Server - query any AWS API in this account."
    "config" (merge (dict "region" .Values.mcpAddons.aws.config.region) $base)
    "llm_instructions" $instructions) -}}
{{- end -}}
{{- toYaml $servers -}}
{{- end -}}
