import { useEffect, useState } from 'react'
import { api } from '../lib/api'

type DocTab = 'setup' | 'aws' | 'pagerduty' | 'ado' | 'salesforce'

const PERMISSIONS_POLICY = JSON.stringify({
  Version: '2012-10-17',
  Statement: [
    { Sid: 'EC2Read', Effect: 'Allow', Action: ['ec2:Describe*', 'ec2:GetConsoleOutput', 'ec2:GetConsoleScreenshot', 'autoscaling:Describe*', 'elasticloadbalancing:Describe*'], Resource: '*' },
    { Sid: 'ECSRead', Effect: 'Allow', Action: ['ecs:Describe*', 'ecs:List*'], Resource: '*' },
    { Sid: 'EKSRead', Effect: 'Allow', Action: ['eks:Describe*', 'eks:List*', 'eks:AccessKubernetesApi'], Resource: '*' },
    { Sid: 'LambdaRead', Effect: 'Allow', Action: ['lambda:GetFunction', 'lambda:GetFunctionConfiguration', 'lambda:GetFunctionEventInvokeConfig', 'lambda:GetPolicy', 'lambda:ListFunctions', 'lambda:ListAliases', 'lambda:ListEventSourceMappings', 'lambda:ListVersionsByFunction'], Resource: '*' },
    { Sid: 'S3Read', Effect: 'Allow', Action: ['s3:GetBucketLocation', 's3:GetBucketVersioning', 's3:GetBucketTagging', 's3:GetBucketPolicy', 's3:GetBucketAcl', 's3:GetBucketLogging', 's3:GetBucketNotification', 's3:GetEncryptionConfiguration', 's3:GetLifecycleConfiguration', 's3:ListAllMyBuckets', 's3:ListBucket'], Resource: '*' },
    { Sid: 'RDSRead', Effect: 'Allow', Action: ['rds:Describe*', 'rds:ListTagsForResource'], Resource: '*' },
    { Sid: 'DynamoDBRead', Effect: 'Allow', Action: ['dynamodb:DescribeTable', 'dynamodb:DescribeContinuousBackups', 'dynamodb:DescribeTimeToLive', 'dynamodb:DescribeGlobalTable', 'dynamodb:ListTables', 'dynamodb:ListTagsOfResource', 'dynamodb:GetItem', 'dynamodb:BatchGetItem', 'dynamodb:Query', 'dynamodb:Scan', 'dynamodb:DescribeStream', 'dynamodb:ListStreams'], Resource: '*' },
    { Sid: 'ElastiCacheRead', Effect: 'Allow', Action: ['elasticache:Describe*', 'elasticache:List*'], Resource: '*' },
    { Sid: 'DAXRead', Effect: 'Allow', Action: ['dax:DescribeClusters', 'dax:DescribeDefaultParameters', 'dax:DescribeEvents', 'dax:DescribeParameterGroups', 'dax:DescribeParameters', 'dax:DescribeSubnetGroups', 'dax:ListTags'], Resource: '*' },
    { Sid: 'CloudWatchRead', Effect: 'Allow', Action: ['cloudwatch:DescribeAlarms', 'cloudwatch:DescribeAlarmHistory', 'cloudwatch:DescribeAnomalyDetectors', 'cloudwatch:GetDashboard', 'cloudwatch:GetMetricData', 'cloudwatch:GetMetricStatistics', 'cloudwatch:GetMetricWidgetImage', 'cloudwatch:ListDashboards', 'cloudwatch:ListMetrics', 'cloudwatch:ListTagsForResource'], Resource: '*' },
    { Sid: 'CloudWatchLogsRead', Effect: 'Allow', Action: ['logs:DescribeLogGroups', 'logs:DescribeLogStreams', 'logs:DescribeMetricFilters', 'logs:DescribeSubscriptionFilters', 'logs:FilterLogEvents', 'logs:GetLogEvents', 'logs:GetLogGroupFields', 'logs:GetLogRecord', 'logs:GetQueryResults', 'logs:ListTagsLogGroup', 'logs:StartQuery', 'logs:StopQuery'], Resource: '*' },
    { Sid: 'CloudTrailRead', Effect: 'Allow', Action: ['cloudtrail:DescribeTrails', 'cloudtrail:GetEventSelectors', 'cloudtrail:GetTrailStatus', 'cloudtrail:ListTrails', 'cloudtrail:LookupEvents'], Resource: '*' },
    { Sid: 'XRayRead', Effect: 'Allow', Action: ['xray:BatchGetTraces', 'xray:GetGroups', 'xray:GetSamplingRules', 'xray:GetServiceGraph', 'xray:GetTraceSummaries'], Resource: '*' },
    { Sid: 'Route53Read', Effect: 'Allow', Action: ['route53:GetHostedZone', 'route53:GetHealthCheck', 'route53:ListHostedZones', 'route53:ListResourceRecordSets', 'route53:ListHealthChecks', 'route53:ListTagsForResource'], Resource: '*' },
    { Sid: 'IAMRead', Effect: 'Allow', Action: ['iam:GetRole', 'iam:GetRolePolicy', 'iam:GetPolicy', 'iam:GetPolicyVersion', 'iam:ListAttachedRolePolicies', 'iam:ListRolePolicies', 'iam:ListRoles', 'iam:ListPolicies'], Resource: '*' },
    { Sid: 'TaggingRead', Effect: 'Allow', Action: ['tag:GetResources', 'tag:GetTagKeys', 'tag:GetTagValues'], Resource: '*' },
  ],
}, null, 2)

const SH_SCRIPT = `#!/bin/bash
# HolmesGPT AWS Account Connection Script
# Creates a scoped read-only IAM role that HolmesGPT can assume from the platform account.
set -euo pipefail

ROLE_NAME="HolmesReadOnly"

# ── Prompt for the HolmesGPT platform IRSA role ARN ─────────────────────────
if [ -z "\${HOLMES_MCP_ROLE_ARN:-}" ]; then
  echo "Enter the HolmesGPT AWS MCP IRSA role ARN from the platform account"
  echo "(e.g. arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp):"
  read -r HOLMES_MCP_ROLE_ARN
fi

if [[ ! "$HOLMES_MCP_ROLE_ARN" =~ ^arn:aws:iam:: ]]; then
  echo "ERROR: Invalid ARN format. Expected arn:aws:iam::<account-id>:role/<role-name>"
  exit 1
fi

# ── Prompt for the EKS OIDC Provider URL ────────────────────────────────────
if [ -z "\${EKS_OIDC_PROVIDER_URL:-}" ]; then
  echo "Enter the EKS OIDC Provider URL from the platform account"
  echo "(e.g. oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE):"
  read -r EKS_OIDC_PROVIDER_URL
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Setting up HolmesGPT role in account: $ACCOUNT_ID"

# ── Register the platform EKS OIDC provider in this account ────────────────
echo "Registering EKS OIDC provider..."
aws iam create-open-id-connect-provider \\
  --url "https://$EKS_OIDC_PROVIDER_URL" \\
  --client-id-list "sts.amazonaws.com" \\
  --thumbprint-list "06b25927c42a721631c1efd9431e648fa62e1e39" \\
  --tags Key=Application,Value=holmesgpt 2>/dev/null \\
  || echo "OIDC provider already exists (OK)"

# ── Trust policy — allows the HolmesGPT platform role to assume this role ───
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowHolmesMCPAssumeRole",
      "Effect": "Allow",
      "Principal": { "AWS": "$HOLMES_MCP_ROLE_ARN" },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "AllowHolmesMCPWebIdentity",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::$ACCOUNT_ID:oidc-provider/$EKS_OIDC_PROVIDER_URL"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "$EKS_OIDC_PROVIDER_URL:aud": "sts.amazonaws.com",
          "$EKS_OIDC_PROVIDER_URL:sub": "system:serviceaccount:holmesgpt:aws-api-mcp-sa"
        }
      }
    }
  ]
}
EOF
)

aws iam create-role \\
  --role-name "$ROLE_NAME" \\
  --assume-role-policy-document "$TRUST_POLICY" \\
  --description "HolmesGPT read-only investigation role" \\
  --tags Key=ManagedBy,Value=holmesgpt-script Key=Application,Value=holmesgpt 2>/dev/null \\
  || aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST_POLICY"

# ── Scoped read-only policy (matches infra/logistics-cross-account/main.tf) ──
POLICY_NAME="\${ROLE_NAME}-triage"
POLICY_DOC=$(cat <<'POLICYEOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2Read",
      "Effect": "Allow",
      "Action": ["ec2:Describe*", "ec2:GetConsoleOutput", "ec2:GetConsoleScreenshot", "autoscaling:Describe*", "elasticloadbalancing:Describe*"],
      "Resource": "*"
    },
    {
      "Sid": "ECSRead",
      "Effect": "Allow",
      "Action": ["ecs:Describe*", "ecs:List*"],
      "Resource": "*"
    },
    {
      "Sid": "EKSRead",
      "Effect": "Allow",
      "Action": ["eks:Describe*", "eks:List*", "eks:AccessKubernetesApi"],
      "Resource": "*"
    },
    {
      "Sid": "LambdaRead",
      "Effect": "Allow",
      "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:GetFunctionEventInvokeConfig", "lambda:GetPolicy", "lambda:ListFunctions", "lambda:ListAliases", "lambda:ListEventSourceMappings", "lambda:ListVersionsByFunction"],
      "Resource": "*"
    },
    {
      "Sid": "S3Read",
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:GetBucketVersioning", "s3:GetBucketTagging", "s3:GetBucketPolicy", "s3:GetBucketAcl", "s3:GetBucketLogging", "s3:GetBucketNotification", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:ListAllMyBuckets", "s3:ListBucket"],
      "Resource": "*"
    },
    {
      "Sid": "RDSRead",
      "Effect": "Allow",
      "Action": ["rds:Describe*", "rds:ListTagsForResource"],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchRead",
      "Effect": "Allow",
      "Action": ["cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory", "cloudwatch:DescribeAnomalyDetectors", "cloudwatch:GetDashboard", "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:GetMetricWidgetImage", "cloudwatch:ListDashboards", "cloudwatch:ListMetrics", "cloudwatch:ListTagsForResource"],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchLogsRead",
      "Effect": "Allow",
      "Action": ["logs:DescribeLogGroups", "logs:DescribeLogStreams", "logs:DescribeMetricFilters", "logs:DescribeSubscriptionFilters", "logs:FilterLogEvents", "logs:GetLogEvents", "logs:GetLogGroupFields", "logs:GetLogRecord", "logs:GetQueryResults", "logs:ListTagsLogGroup", "logs:StartQuery", "logs:StopQuery"],
      "Resource": "*"
    },
    {
      "Sid": "CloudTrailRead",
      "Effect": "Allow",
      "Action": ["cloudtrail:DescribeTrails", "cloudtrail:GetEventSelectors", "cloudtrail:GetTrailStatus", "cloudtrail:ListTrails", "cloudtrail:LookupEvents"],
      "Resource": "*"
    },
    {
      "Sid": "XRayRead",
      "Effect": "Allow",
      "Action": ["xray:BatchGetTraces", "xray:GetGroups", "xray:GetSamplingRules", "xray:GetServiceGraph", "xray:GetTraceSummaries"],
      "Resource": "*"
    },
    {
      "Sid": "Route53Read",
      "Effect": "Allow",
      "Action": ["route53:GetHostedZone", "route53:GetHealthCheck", "route53:ListHostedZones", "route53:ListResourceRecordSets", "route53:ListHealthChecks", "route53:ListTagsForResource"],
      "Resource": "*"
    },
    {
      "Sid": "IAMRead",
      "Effect": "Allow",
      "Action": ["iam:GetRole", "iam:GetRolePolicy", "iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies", "iam:ListRoles", "iam:ListPolicies"],
      "Resource": "*"
    },
    {
      "Sid": "TaggingRead",
      "Effect": "Allow",
      "Action": ["tag:GetResources", "tag:GetTagKeys", "tag:GetTagValues"],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDBRead",
      "Effect": "Allow",
      "Action": ["dynamodb:DescribeTable", "dynamodb:DescribeContinuousBackups", "dynamodb:DescribeTimeToLive", "dynamodb:DescribeGlobalTable", "dynamodb:ListTables", "dynamodb:ListTagsOfResource", "dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeStream", "dynamodb:ListStreams"],
      "Resource": "*"
    },
    {
      "Sid": "ElastiCacheRead",
      "Effect": "Allow",
      "Action": ["elasticache:Describe*", "elasticache:List*"],
      "Resource": "*"
    },
    {
      "Sid": "DAXRead",
      "Effect": "Allow",
      "Action": ["dax:DescribeClusters", "dax:DescribeDefaultParameters", "dax:DescribeEvents", "dax:DescribeParameterGroups", "dax:DescribeParameters", "dax:DescribeSubnetGroups", "dax:ListTags"],
      "Resource": "*"
    }
  ]
}
POLICYEOF
)

# Create or update the custom policy
EXISTING_POLICY_ARN=$(aws iam list-policies --scope Local --query "Policies[?PolicyName=='\${POLICY_NAME}'].Arn" --output text 2>/dev/null || true)
if [ -n "$EXISTING_POLICY_ARN" ] && [ "$EXISTING_POLICY_ARN" != "None" ]; then
  # Delete old versions if at limit, then create new version
  OLD_VERSIONS=$(aws iam list-policy-versions --policy-arn "$EXISTING_POLICY_ARN" --query "Versions[?!IsDefaultVersion].VersionId" --output text)
  for v in $OLD_VERSIONS; do
    aws iam delete-policy-version --policy-arn "$EXISTING_POLICY_ARN" --version-id "$v" 2>/dev/null || true
  done
  aws iam create-policy-version --policy-arn "$EXISTING_POLICY_ARN" --policy-document "$POLICY_DOC" --set-as-default > /dev/null
  POLICY_ARN="$EXISTING_POLICY_ARN"
else
  POLICY_ARN=$(aws iam create-policy --policy-name "$POLICY_NAME" --policy-document "$POLICY_DOC" --description "Scoped read-only permissions for HolmesGPT incident triage" --query Policy.Arn --output text)
fi

# Detach ReadOnlyAccess if previously attached (from older script version)
aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/ReadOnlyAccess" 2>/dev/null || true

aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$POLICY_ARN"

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)
echo ""
echo "===== SUCCESS ====="
echo "Role ARN:    $ROLE_ARN"
echo "Account ID:  $ACCOUNT_ID"
echo ""
echo "Next steps:"
echo "  1. Copy the Role ARN above"
echo "  2. In HolmesGPT, go to Integrations -> add an AWS integration -> paste the Role ARN"
`

const PS1_SCRIPT = `# HolmesGPT AWS Account Connection Script (PowerShell)
# Creates a scoped read-only IAM role that HolmesGPT can assume from the platform account.
$ErrorActionPreference = "Stop"

$RoleName = "HolmesReadOnly"

# ── Prompt for the HolmesGPT platform IRSA role ARN ─────────────────────────
if (-not $env:HOLMES_MCP_ROLE_ARN) {
    $HolmesMcpRoleArn = Read-Host "Enter the HolmesGPT AWS MCP IRSA role ARN from the platform account\`n(e.g. arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp)"
} else {
    $HolmesMcpRoleArn = $env:HOLMES_MCP_ROLE_ARN
}

if ($HolmesMcpRoleArn -notmatch "^arn:aws:iam::") {
    Write-Error "Invalid ARN format. Expected arn:aws:iam::<account-id>:role/<role-name>"
    exit 1
}

# ── Prompt for the EKS OIDC Provider URL ────────────────────────────────────
if (-not $env:EKS_OIDC_PROVIDER_URL) {
    $EksOidcProviderUrl = Read-Host "Enter the EKS OIDC Provider URL from the platform account\`n(e.g. oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE)"
} else {
    $EksOidcProviderUrl = $env:EKS_OIDC_PROVIDER_URL
}

$AccountId = (aws sts get-caller-identity --query Account --output text)
Write-Host "Setting up HolmesGPT role in account: $AccountId"

# ── Register the platform EKS OIDC provider in this account ────────────────
Write-Host "Registering EKS OIDC provider..."
try {
    aws iam create-open-id-connect-provider --url "https://$EksOidcProviderUrl" --client-id-list "sts.amazonaws.com" --thumbprint-list "06b25927c42a721631c1efd9431e648fa62e1e39" --tags Key=Application,Value=holmesgpt | Out-Null
} catch {
    Write-Host "OIDC provider already exists (OK)"
}

# ── Trust policy — allows the HolmesGPT platform role to assume this role ───
$TrustPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowHolmesMCPAssumeRole",
      "Effect": "Allow",
      "Principal": { "AWS": "$HolmesMcpRoleArn" },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "AllowHolmesMCPWebIdentity",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::$AccountId:oidc-provider/$EksOidcProviderUrl"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "$EksOidcProviderUrl${'`'}:aud": "sts.amazonaws.com",
          "$EksOidcProviderUrl${'`'}:sub": "system:serviceaccount:holmesgpt:aws-api-mcp-sa"
        }
      }
    }
  ]
}
"@

try {
    aws iam create-role --role-name $RoleName --assume-role-policy-document $TrustPolicy --description "HolmesGPT read-only investigation role" --tags Key=ManagedBy,Value=holmesgpt-script Key=Application,Value=holmesgpt | Out-Null
} catch {
    aws iam update-assume-role-policy --role-name $RoleName --policy-document $TrustPolicy
}

# ── Scoped read-only policy (matches infra/logistics-cross-account/main.tf) ──
$PolicyName = "$RoleName-triage"
$PolicyDoc = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid":"EC2Read","Effect":"Allow","Action":["ec2:Describe*","ec2:GetConsoleOutput","ec2:GetConsoleScreenshot","autoscaling:Describe*","elasticloadbalancing:Describe*"],"Resource":"*"},
    {"Sid":"ECSRead","Effect":"Allow","Action":["ecs:Describe*","ecs:List*"],"Resource":"*"},
    {"Sid":"EKSRead","Effect":"Allow","Action":["eks:Describe*","eks:List*","eks:AccessKubernetesApi"],"Resource":"*"},
    {"Sid":"LambdaRead","Effect":"Allow","Action":["lambda:GetFunction","lambda:GetFunctionConfiguration","lambda:GetFunctionEventInvokeConfig","lambda:GetPolicy","lambda:ListFunctions","lambda:ListAliases","lambda:ListEventSourceMappings","lambda:ListVersionsByFunction"],"Resource":"*"},
    {"Sid":"S3Read","Effect":"Allow","Action":["s3:GetBucketLocation","s3:GetBucketVersioning","s3:GetBucketTagging","s3:GetBucketPolicy","s3:GetBucketAcl","s3:GetBucketLogging","s3:GetBucketNotification","s3:GetEncryptionConfiguration","s3:GetLifecycleConfiguration","s3:ListAllMyBuckets","s3:ListBucket"],"Resource":"*"},
    {"Sid":"RDSRead","Effect":"Allow","Action":["rds:Describe*","rds:ListTagsForResource"],"Resource":"*"},
    {"Sid":"CloudWatchRead","Effect":"Allow","Action":["cloudwatch:DescribeAlarms","cloudwatch:DescribeAlarmHistory","cloudwatch:DescribeAnomalyDetectors","cloudwatch:GetDashboard","cloudwatch:GetMetricData","cloudwatch:GetMetricStatistics","cloudwatch:GetMetricWidgetImage","cloudwatch:ListDashboards","cloudwatch:ListMetrics","cloudwatch:ListTagsForResource"],"Resource":"*"},
    {"Sid":"CloudWatchLogsRead","Effect":"Allow","Action":["logs:DescribeLogGroups","logs:DescribeLogStreams","logs:DescribeMetricFilters","logs:DescribeSubscriptionFilters","logs:FilterLogEvents","logs:GetLogEvents","logs:GetLogGroupFields","logs:GetLogRecord","logs:GetQueryResults","logs:ListTagsLogGroup","logs:StartQuery","logs:StopQuery"],"Resource":"*"},
    {"Sid":"CloudTrailRead","Effect":"Allow","Action":["cloudtrail:DescribeTrails","cloudtrail:GetEventSelectors","cloudtrail:GetTrailStatus","cloudtrail:ListTrails","cloudtrail:LookupEvents"],"Resource":"*"},
    {"Sid":"XRayRead","Effect":"Allow","Action":["xray:BatchGetTraces","xray:GetGroups","xray:GetSamplingRules","xray:GetServiceGraph","xray:GetTraceSummaries"],"Resource":"*"},
    {"Sid":"Route53Read","Effect":"Allow","Action":["route53:GetHostedZone","route53:GetHealthCheck","route53:ListHostedZones","route53:ListResourceRecordSets","route53:ListHealthChecks","route53:ListTagsForResource"],"Resource":"*"},
    {"Sid":"IAMRead","Effect":"Allow","Action":["iam:GetRole","iam:GetRolePolicy","iam:GetPolicy","iam:GetPolicyVersion","iam:ListAttachedRolePolicies","iam:ListRolePolicies","iam:ListRoles","iam:ListPolicies"],"Resource":"*"},
    {"Sid":"TaggingRead","Effect":"Allow","Action":["tag:GetResources","tag:GetTagKeys","tag:GetTagValues"],"Resource":"*"},
    {"Sid":"DynamoDBRead","Effect":"Allow","Action":["dynamodb:DescribeTable","dynamodb:DescribeContinuousBackups","dynamodb:DescribeTimeToLive","dynamodb:DescribeGlobalTable","dynamodb:ListTables","dynamodb:ListTagsOfResource","dynamodb:GetItem","dynamodb:BatchGetItem","dynamodb:Query","dynamodb:Scan","dynamodb:DescribeStream","dynamodb:ListStreams"],"Resource":"*"},
    {"Sid":"ElastiCacheRead","Effect":"Allow","Action":["elasticache:Describe*","elasticache:List*"],"Resource":"*"},
    {"Sid":"DAXRead","Effect":"Allow","Action":["dax:DescribeClusters","dax:DescribeDefaultParameters","dax:DescribeEvents","dax:DescribeParameterGroups","dax:DescribeParameters","dax:DescribeSubnetGroups","dax:ListTags"],"Resource":"*"}
  ]
}
'@

# Create or update the custom policy
$ExistingPolicyArn = (aws iam list-policies --scope Local --query "Policies[?PolicyName=='$PolicyName'].Arn" --output text 2>$null)
if ($ExistingPolicyArn -and $ExistingPolicyArn -ne "None") {
    $OldVersions = (aws iam list-policy-versions --policy-arn $ExistingPolicyArn --query "Versions[?!IsDefaultVersion].VersionId" --output text) -split "\\s+"
    foreach ($v in $OldVersions) {
        if ($v) { aws iam delete-policy-version --policy-arn $ExistingPolicyArn --version-id $v 2>$null }
    }
    aws iam create-policy-version --policy-arn $ExistingPolicyArn --policy-document $PolicyDoc --set-as-default | Out-Null
    $PolicyArn = $ExistingPolicyArn
} else {
    $PolicyArn = (aws iam create-policy --policy-name $PolicyName --policy-document $PolicyDoc --description "Scoped read-only permissions for HolmesGPT incident triage" --query Policy.Arn --output text)
}

# Detach ReadOnlyAccess if previously attached (from older script version)
try { aws iam detach-role-policy --role-name $RoleName --policy-arn "arn:aws:iam::aws:policy/ReadOnlyAccess" 2>$null } catch {}

aws iam attach-role-policy --role-name $RoleName --policy-arn $PolicyArn

$RoleArn = (aws iam get-role --role-name $RoleName --query Role.Arn --output text)
Write-Host ""
Write-Host "===== SUCCESS ====="
Write-Host "Role ARN:    $RoleArn"
Write-Host "Account ID:  $AccountId"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Copy the Role ARN above"
Write-Host "  2. In HolmesGPT, go to Integrations -> add an AWS integration -> paste the Role ARN"
`

function downloadScript(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

const tabs: { id: DocTab; label: string }[] = [
  { id: 'setup', label: 'Setup Guide' },
  { id: 'aws', label: 'AWS Account' },
  { id: 'pagerduty', label: 'PagerDuty' },
  { id: 'ado', label: 'Azure DevOps' },
  { id: 'salesforce', label: 'Salesforce' },
]

interface StepListProps {
  steps: (string | React.ReactNode)[]
}

function StepList({ steps }: StepListProps) {
  return (
    <ol className="space-y-3 text-sm text-gray-700">
      {steps.map((step, i) => (
        <li key={i} className="flex gap-3">
          <span className="flex-shrink-0 w-6 h-6 rounded-full bg-pdi-sky/10 text-pdi-sky text-xs font-bold flex items-center justify-center mt-0.5">
            {i + 1}
          </span>
          <span className="leading-relaxed">{step}</span>
        </li>
      ))}
    </ol>
  )
}

interface CopyableUrlProps {
  url: string
  urlKey: string
  copied: string | null
  onCopy: (text: string, key: string) => void
}

function CopyableUrl({ url, urlKey, copied, onCopy }: CopyableUrlProps) {
  return (
    <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 font-mono text-sm text-gray-700">
      <span className="flex-1 truncate">{url}</span>
      <button
        onClick={() => onCopy(url, urlKey)}
        className={`flex-shrink-0 px-3 py-1 text-xs font-medium rounded-md transition-colors ${
          copied === urlKey
            ? 'bg-pdi-grass/10 text-pdi-grass border border-pdi-grass/20'
            : 'bg-white text-gray-600 border border-gray-300 hover:bg-gray-50'
        }`}
      >
        {copied === urlKey ? 'Copied!' : 'Copy'}
      </button>
    </div>
  )
}

export default function Docs() {
  const [activeTab, setActiveTab] = useState<DocTab>('setup')
  const [copied, setCopied] = useState<string | null>(null)
  const [irsaRole, setIrsaRole] = useState<string>('')
  const [oidcUrl, setOidcUrl] = useState<string>('')

  useEffect(() => {
    api.getAwsAccounts().then((data) => {
      setIrsaRole(data.irsa_role || '')
      setOidcUrl(data.eks_oidc_url || '')
    }).catch(() => {})
  }, [])

  function copyText(text: string, key: string) {
    navigator.clipboard.writeText(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-gray-900">Documentation</h1>
          <p className="mt-1 text-sm text-gray-500">
            Step-by-step guides for connecting your infrastructure to HolmesGPT.
          </p>
        </div>

        {/* Tab bar */}
        <div className="border-b border-gray-200 mb-6">
          <div className="flex gap-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
                  activeTab === tab.id
                    ? 'border-pdi-sky text-pdi-sky'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content panel */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">

          {/* Setup Guide Tab */}
          {activeTab === 'setup' && (
            <div className="space-y-8">
              <div>
                <h2 className="text-base font-semibold text-gray-900 mb-1">Getting Started with HolmesGPT</h2>
                <p className="text-sm text-gray-500">
                  HolmesGPT connects to your infrastructure and uses AI to investigate issues. This guide explains the three building blocks you need to configure before running investigations.
                </p>
              </div>

              {/* Concept overview */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-7 h-7 rounded-full bg-pdi-sky/10 text-pdi-sky text-xs font-bold flex items-center justify-center flex-shrink-0">1</span>
                    <span className="text-sm font-semibold text-gray-900">Tools &amp; Integrations</span>
                  </div>
                  <p className="text-xs text-gray-500 leading-relaxed">
                    Connect data sources — Kubernetes clusters, Prometheus, Grafana, AWS accounts, ADO, Salesforce, and more. Each integration gives HolmesGPT a set of tools it can call during an investigation.
                  </p>
                </div>
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-7 h-7 rounded-full bg-pdi-sky/10 text-pdi-sky text-xs font-bold flex items-center justify-center flex-shrink-0">2</span>
                    <span className="text-sm font-semibold text-gray-900">Instances</span>
                  </div>
                  <p className="text-xs text-gray-500 leading-relaxed">
                    Named connections to specific environments (e.g. "Retail K8s Prod", "Logistics AWS"). Each instance has a type, credentials, and optional tags. Instances without tags are <em>global</em> — always available.
                  </p>
                </div>
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-7 h-7 rounded-full bg-pdi-sky/10 text-pdi-sky text-xs font-bold flex items-center justify-center flex-shrink-0">3</span>
                    <span className="text-sm font-semibold text-gray-900">Projects</span>
                  </div>
                  <p className="text-xs text-gray-500 leading-relaxed">
                    Named scopes that group instances using tag filters. When you select a project in Chat or Investigate, HolmesGPT only uses the instances that match the project's tag filter plus all global instances.
                  </p>
                </div>
              </div>

              {/* How they fit together */}
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4">
                <p className="text-sm font-semibold text-blue-900 mb-2">How they fit together</p>
                <p className="text-sm text-blue-800 leading-relaxed">
                  Think of it as a funnel: <strong>Integrations</strong> define what types of data HolmesGPT can access. <strong>Instances</strong> are the actual connections to your environments, tagged by team or line of business. <strong>Projects</strong> use tag filters to select which instances are in scope for a given investigation — so the Retail team only sees Retail instances, and the Logistics team only sees Logistics instances.
                </p>
              </div>

              {/* Section: Tools & Integrations */}
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-gray-900 border-b border-gray-100 pb-2">Step 1 — Enable Tools &amp; Integrations</h3>
                <p className="text-sm text-gray-600">
                  Go to <strong>Integrations</strong> in the sidebar. Each card represents a toolset. Toggle integrations on or off and supply any required configuration (API URLs, credentials, etc.).
                </p>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {[
                    { name: 'Kubernetes', desc: 'Pod logs, events, resource status' },
                    { name: 'Prometheus / Metrics', desc: 'Metric queries and alerting rules' },
                    { name: 'Grafana', desc: 'Dashboards, Loki logs, Tempo traces' },
                    { name: 'AWS', desc: 'CloudWatch, ECS, RDS, Lambda, and more' },
                    { name: 'Azure DevOps', desc: 'Work items, pipelines, repositories' },
                    { name: 'Salesforce', desc: 'Cases, accounts, and custom objects' },
                  ].map((item) => (
                    <div key={item.name} className="flex items-start gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2.5">
                      <svg className="w-4 h-4 text-pdi-grass mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                      <div>
                        <p className="text-xs font-semibold text-gray-800">{item.name}</p>
                        <p className="text-xs text-gray-500">{item.desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-gray-400">
                  Some integrations (AWS, ADO, Salesforce) require additional setup — see the other tabs in this Docs page for step-by-step instructions.
                </p>
              </div>

              {/* Section: Instances */}
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-gray-900 border-b border-gray-100 pb-2">Step 2 — Create Instances</h3>
                <p className="text-sm text-gray-600">
                  Go to <strong>Instances</strong> in the sidebar. An instance is a named connection to a specific environment. Create one instance per environment you want HolmesGPT to investigate.
                </p>
                <StepList
                  steps={[
                    <>Click <strong>New Instance</strong>.</>,
                    <>Choose a <strong>Type</strong> — this determines which toolset the instance uses (e.g. <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">kubernetes</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">aws_api</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">prometheus/metrics</code>).</>,
                    <>Give it a descriptive <strong>Name</strong> (e.g. <em>"Retail K8s Prod"</em>, <em>"Logistics AWS us-east-1"</em>).</>,
                    <>Add <strong>Tags</strong> as key=value pairs to identify which team or line of business this instance belongs to (e.g. <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">lob=retail</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">env=prod</code>). Leave tags empty to make the instance <em>global</em>.</>,
                    <>Supply any required credentials (Secret ARN, MCP URL, or AWS account list depending on the type).</>,
                    <>Click <strong>Save</strong>.</>,
                  ]}
                />
                <div className="rounded-lg border border-amber-100 bg-amber-50 p-3">
                  <p className="text-xs font-semibold text-amber-800 mb-1">Global vs. tagged instances</p>
                  <p className="text-xs text-amber-700 leading-relaxed">
                    Instances with <strong>no tags</strong> are <em>global</em> — they are always included in every investigation regardless of which project is selected. Use global instances for shared infrastructure (e.g. a central Prometheus or Grafana). Use tagged instances for environment-specific connections.
                  </p>
                </div>
              </div>

              {/* Section: Projects */}
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-gray-900 border-b border-gray-100 pb-2">Step 3 — Create Projects</h3>
                <p className="text-sm text-gray-600">
                  Go to <strong>Projects</strong> in the sidebar. A project defines a tag filter that selects which tagged instances are in scope. Global (untagged) instances are always included.
                </p>
                <StepList
                  steps={[
                    <>Click <strong>New Project</strong>.</>,
                    <>Enter a <strong>Name</strong> and optional description (e.g. <em>"Retail Cloud"</em>).</>,
                    <>Add one or more <strong>tag filter</strong> rows. Each row is a key=value pair (e.g. <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">lob=retail</code>).</>,
                    <>Choose the match logic: <strong>AND</strong> (all tags must match) or <strong>OR</strong> (any tag must match).</>,
                    <>The <strong>Resolved Instances</strong> preview shows which instances will be used — verify it looks correct before saving.</>,
                    <>Click <strong>Save Project</strong>.</>,
                  ]}
                />
                <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-2">
                  <p className="text-xs font-semibold text-gray-700">Tag filter examples</p>
                  <div className="space-y-1.5">
                    {[
                      { filter: 'lob=retail  (AND)', result: 'All instances tagged lob=retail + all global instances' },
                      { filter: 'lob=retail, env=prod  (AND)', result: 'Instances tagged with both lob=retail AND env=prod + globals' },
                      { filter: 'lob=retail, lob=logistics  (OR)', result: 'Instances tagged lob=retail OR lob=logistics + globals' },
                      { filter: '(empty filter)', result: 'Only global (untagged) instances' },
                    ].map((ex) => (
                      <div key={ex.filter} className="flex gap-3 text-xs">
                        <code className="flex-shrink-0 bg-white border border-gray-200 rounded px-2 py-0.5 text-gray-700 font-mono">{ex.filter}</code>
                        <span className="text-gray-500 leading-relaxed">{ex.result}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Section: Using a project */}
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-gray-900 border-b border-gray-100 pb-2">Step 4 — Use a Project in Chat or Investigate</h3>
                <p className="text-sm text-gray-600">
                  Once your instances and projects are set up, select a project from the sidebar dropdown before starting a chat or investigation. HolmesGPT will only use the instances resolved by that project's tag filter.
                </p>
                <div className="rounded-lg border border-blue-100 bg-blue-50 p-3">
                  <p className="text-xs text-blue-800 leading-relaxed">
                    <strong>Tip:</strong> If no project is selected, HolmesGPT uses all global (untagged) instances. This is useful for quick ad-hoc investigations that don't need project scoping.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* AWS Account Tab */}
          {activeTab === 'aws' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-base font-semibold text-gray-900 mb-1">Connect an AWS Account</h2>
                <p className="text-sm text-gray-500">
                  This script creates a read-only IAM role in your AWS account that HolmesGPT uses to investigate issues.
                  No write permissions are granted — HolmesGPT can only read resource configurations and metrics.
                </p>
              </div>

              {/* IRSA Role ARN display */}
              {irsaRole && (
                <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 space-y-2">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">HolmesGPT Platform IRSA Role ARN</p>
                  <div className="flex items-center gap-2 bg-white border border-blue-200 rounded-lg px-4 py-2.5 font-mono text-sm text-gray-700">
                    <span className="flex-1 truncate select-all">{irsaRole}</span>
                    <button
                      onClick={() => copyText(irsaRole, 'irsa')}
                      className={`flex-shrink-0 px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                        copied === 'irsa'
                          ? 'bg-pdi-grass/10 text-pdi-grass border border-pdi-grass/20'
                          : 'bg-white text-gray-600 border border-gray-300 hover:bg-gray-50'
                      }`}
                    >
                      {copied === 'irsa' ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  {oidcUrl && (
                    <div className="mt-3">
                      <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">EKS OIDC Provider URL</p>
                      <div className="flex items-center gap-2 bg-white border border-blue-200 rounded-lg px-4 py-2.5 font-mono text-sm text-gray-700 mt-1">
                        <span className="flex-1 truncate select-all">{oidcUrl}</span>
                        <button
                          onClick={() => copyText(oidcUrl, 'oidc')}
                          className={`flex-shrink-0 px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                            copied === 'oidc'
                              ? 'bg-pdi-grass/10 text-pdi-grass border border-pdi-grass/20'
                              : 'bg-white text-gray-600 border border-gray-300 hover:bg-gray-50'
                          }`}
                        >
                          {copied === 'oidc' ? 'Copied!' : 'Copy'}
                        </button>
                      </div>
                    </div>
                  )}
                  <p className="text-xs text-blue-600">The script will ask for both values. Copy them before running.</p>
                </div>
              )}

              <StepList
                steps={[
                  <>Ensure the <strong>AWS CLI</strong> is installed and configured for the target account (<code className="bg-gray-100 px-1 py-0.5 rounded text-xs">aws configure</code>).</>,
                  <>Copy the <strong>IRSA Role ARN</strong> and <strong>EKS OIDC Provider URL</strong> shown above — the script will prompt for both.</>,
                  <>Download the setup script for your operating system using the buttons below.</>,
                  <>Run the script — it will register the EKS OIDC provider, create a role named <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">HolmesReadOnly</code> with scoped read-only permissions and web identity trust, and print its ARN.</>,
                  <>Copy the <strong>Role ARN</strong> printed by the script.</>,
                  <>In HolmesGPT, go to <strong>Integrations</strong> → add an AWS integration → paste the Role ARN.</>,
                ]}
              />

              {/* Download buttons */}
              <div>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">Download Setup Script</p>
                <div className="flex flex-wrap gap-3">
                  <button
                    onClick={() => downloadScript('connect-aws.sh', SH_SCRIPT)}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                    </svg>
                    Download for Linux / Mac (.sh)
                  </button>
                  <button
                    onClick={() => downloadScript('connect-aws.ps1', PS1_SCRIPT)}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                    </svg>
                    Download for Windows (.ps1)
                  </button>
                </div>
              </div>

              {/* Reference policy downloads */}
              {(irsaRole || oidcUrl) && (
                <div>
                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Reference Policy Documents</p>
                  <p className="text-xs text-gray-500 mb-3">
                    If you prefer to set up the IAM resources manually (or need to share with your CloudOps team), download the JSON documents below.
                    Replace <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">{'<TARGET_ACCOUNT_ID>'}</code> with the target AWS account ID.
                  </p>
                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={() => {
                        const placeholder = '<' + 'TARGET_ACCOUNT_ID' + '>'
                        const oidcFallback = '<' + 'EKS_OIDC_PROVIDER_URL' + '>'
                        const oidc = oidcUrl || oidcFallback
                        const trustPolicy = JSON.stringify({
                          Version: '2012-10-17',
                          Statement: [
                            {
                              Sid: 'AllowHolmesMCPAssumeRole',
                              Effect: 'Allow',
                              Principal: { AWS: irsaRole || ('<' + 'HOLMES_MCP_ROLE_ARN' + '>') },
                              Action: 'sts:AssumeRole',
                            },
                            {
                              Sid: 'AllowHolmesMCPWebIdentity',
                              Effect: 'Allow',
                              Principal: {
                                Federated: `arn:aws:iam::${placeholder}:oidc-provider/${oidc}`,
                              },
                              Action: 'sts:AssumeRoleWithWebIdentity',
                              Condition: {
                                StringEquals: {
                                  [`${oidc}:aud`]: 'sts.amazonaws.com',
                                  [`${oidc}:sub`]: 'system:serviceaccount:holmesgpt:aws-api-mcp-sa',
                                },
                              },
                            },
                          ],
                        }, null, 2)
                        downloadScript('trust-policy.json', trustPolicy)
                      }}
                      className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                      </svg>
                      Trust Policy (.json)
                    </button>
                    <button
                      onClick={() => downloadScript('permissions-policy.json', PERMISSIONS_POLICY)}
                      className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                      </svg>
                      Permissions Policy (.json)
                    </button>
                  </div>
                </div>
              )}

              {/* Script preview */}
              <div>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Script Preview (Linux / Mac)</p>
                <pre className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-700 overflow-x-auto leading-relaxed">
                  {SH_SCRIPT.split('\n').slice(0, 45).join('\n')}
                  {'\n...'}
                </pre>
              </div>
            </div>
          )}

          {/* PagerDuty Tab */}
          {activeTab === 'pagerduty' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-base font-semibold text-gray-900 mb-1">Add a PagerDuty Webhook</h2>
                <p className="text-sm text-gray-500">
                  Connect PagerDuty so HolmesGPT automatically investigates incidents when they are triggered, acknowledged, or resolved.
                </p>
              </div>

              <StepList
                steps={[
                  <>Log in to PagerDuty → <strong>Integrations</strong> → <strong>Generic Webhooks (V3)</strong>.</>,
                  <>Click <strong>+ New Webhook</strong>.</>,
                  <>Set the <strong>Endpoint URL</strong> to the URL shown below.</>,
                  <>Select event types: <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">incident.triggered</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">incident.acknowledged</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">incident.resolved</code>.</>,
                  <>Click <strong>Save</strong> and copy the <strong>Webhook Secret</strong> shown after saving.</>,
                  <>Give the secret to your HolmesGPT administrator — they must store it in the <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">holmes-api-keys</code> Kubernetes secret as <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">PAGERDUTY_WEBHOOK_SECRET</code>.</>,
                ]}
              />

              <div>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Webhook Endpoint URL</p>
                <CopyableUrl
                  url="https://<your-holmesgpt-url>/api/webhook/pagerduty"
                  urlKey="pagerduty"
                  copied={copied}
                  onCopy={copyText}
                />
                <p className="mt-2 text-xs text-gray-400">Replace <code className="bg-gray-100 px-1 py-0.5 rounded">&lt;your-holmesgpt-url&gt;</code> with your HolmesGPT deployment URL.</p>
              </div>

              {/* Authentication details */}
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 space-y-3">
                <p className="text-sm font-semibold text-blue-900">How authentication works</p>
                <p className="text-sm text-blue-800">
                  HolmesGPT uses <strong>HMAC-SHA256 signature verification</strong>. PagerDuty signs every request with the webhook secret and sends the signature in the <code className="bg-blue-100 px-1 py-0.5 rounded text-xs">x-pagerduty-signature</code> header (format: <code className="bg-blue-100 px-1 py-0.5 rounded text-xs">v1=&lt;hex-digest&gt;</code>). HolmesGPT recomputes the HMAC over the raw request body and rejects any request where the signatures do not match.
                </p>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">Header sent by PagerDuty</p>
                  <pre className="bg-white border border-blue-200 rounded px-3 py-2 text-xs text-gray-700 overflow-x-auto">x-pagerduty-signature: v1=&lt;hmac-sha256-hex&gt;</pre>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">Admin setup (k8s secret)</p>
                  <pre className="bg-white border border-blue-200 rounded px-3 py-2 text-xs text-gray-700 overflow-x-auto">{`kubectl patch secret holmes-api-keys -n holmesgpt \\\n  --type=merge -p '{"stringData":{"PAGERDUTY_WEBHOOK_SECRET":"<secret>"}}'`}</pre>
                </div>
                <p className="text-xs text-blue-600">Authentication is enforced when <code className="bg-blue-100 px-1 py-0.5 rounded">PAGERDUTY_WEBHOOK_SECRET</code> is set and Development Mode is off. To bypass authentication during testing, enable <strong>Development Mode</strong> in <strong>Settings → Webhooks</strong>.</p>
              </div>
            </div>
          )}

          {/* ADO Tab */}
          {activeTab === 'ado' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-base font-semibold text-gray-900 mb-1">Add an Azure DevOps Webhook</h2>
                <p className="text-sm text-gray-500">
                  Connect Azure DevOps so HolmesGPT can investigate work items and receive notifications when items are created or updated.
                </p>
              </div>

              <StepList
                steps={[
                  <>In Azure DevOps, go to <strong>Project Settings</strong> → <strong>Service Hooks</strong>.</>,
                  <>Click <strong>+</strong> → select <strong>Web Hooks</strong>.</>,
                  <>Choose a trigger: <strong>Work item created</strong> or <strong>Work item updated</strong>.</>,
                  <>Set the <strong>URL</strong> to the endpoint shown below.</>,
                  <>In the <strong>Basic authentication</strong> section of the service hook, enter a username and password of your choice — these are the credentials HolmesGPT will require on every incoming request.</>,
                  <>Give the username and password to your HolmesGPT administrator — they must store them in the <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">holmes-api-keys</code> Kubernetes secret as <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">ADO_WEBHOOK_USERNAME</code> and <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">ADO_WEBHOOK_PASSWORD</code>.</>,
                  <>Click <strong>Finish</strong> to save the service hook.</>,
                ]}
              />

              <div>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Webhook Endpoint URL</p>
                <CopyableUrl
                  url="https://<your-holmesgpt-url>/api/webhook/ado"
                  urlKey="ado"
                  copied={copied}
                  onCopy={copyText}
                />
                <p className="mt-2 text-xs text-gray-400">Replace <code className="bg-gray-100 px-1 py-0.5 rounded">&lt;your-holmesgpt-url&gt;</code> with your HolmesGPT deployment URL.</p>
              </div>

              {/* Authentication details */}
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 space-y-3">
                <p className="text-sm font-semibold text-blue-900">How authentication works</p>
                <p className="text-sm text-blue-800">
                  HolmesGPT uses <strong>HTTP Basic Authentication</strong>. Azure DevOps sends an <code className="bg-blue-100 px-1 py-0.5 rounded text-xs">Authorization</code> header with every request containing the base64-encoded credentials you configured in the service hook. HolmesGPT validates the username and password using a timing-safe comparison and rejects any request with missing or incorrect credentials.
                </p>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">Header sent by Azure DevOps</p>
                  <pre className="bg-white border border-blue-200 rounded px-3 py-2 text-xs text-gray-700 overflow-x-auto">Authorization: Basic &lt;base64(username:password)&gt;</pre>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">Admin setup (k8s secret)</p>
                  <pre className="bg-white border border-blue-200 rounded px-3 py-2 text-xs text-gray-700 overflow-x-auto">{`kubectl patch secret holmes-api-keys -n holmesgpt \\\n  --type=merge -p '{"stringData":{"ADO_WEBHOOK_USERNAME":"<user>","ADO_WEBHOOK_PASSWORD":"<pass>"}}'`}</pre>
                </div>
                <p className="text-xs text-blue-600">Authentication is enforced when <code className="bg-blue-100 px-1 py-0.5 rounded">ADO_WEBHOOK_USERNAME</code> or <code className="bg-blue-100 px-1 py-0.5 rounded">ADO_WEBHOOK_PASSWORD</code> is set and Development Mode is off. To bypass authentication during testing, enable <strong>Development Mode</strong> in <strong>Settings → Webhooks</strong>.</p>
              </div>
            </div>
          )}

          {/* Salesforce Tab */}
          {activeTab === 'salesforce' && (
            <div className="space-y-6">
              <div>
                <h2 className="text-base font-semibold text-gray-900 mb-1">Add a Salesforce Webhook</h2>
                <p className="text-sm text-gray-500">
                  Connect Salesforce so HolmesGPT receives case updates via outbound messages and can investigate customer issues automatically.
                </p>
              </div>

              <StepList
                steps={[
                  <>In Salesforce Setup, search for <strong>Outbound Messages</strong> in the Quick Find box.</>,
                  <>Click <strong>New Outbound Message</strong> for the <strong>Case</strong> object.</>,
                  <>Set the <strong>Endpoint URL</strong> to the URL shown below.</>,
                  <>Select fields to include: <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">CaseNumber</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">Subject</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">Status</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">Priority</code>, <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">Description</code>.</>,
                  <>If using a Flow HTTP callout instead of an outbound message, add a custom HTTP header <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">X-Salesforce-Token</code> set to the shared token (see authentication section below).</>,
                  <>Create a <strong>Workflow Rule</strong> (or Flow) that triggers this outbound message on case create or update.</>,
                  <><strong>Activate</strong> the workflow rule.</>,
                  <>Give the shared token to your HolmesGPT administrator — they must store it in the <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">holmes-api-keys</code> Kubernetes secret as <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">SALESFORCE_WEBHOOK_TOKEN</code>.</>,
                ]}
              />

              <div>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Webhook Endpoint URL</p>
                <CopyableUrl
                  url="https://<your-holmesgpt-url>/api/webhook/salesforce"
                  urlKey="salesforce"
                  copied={copied}
                  onCopy={copyText}
                />
                <p className="mt-2 text-xs text-gray-400">Replace <code className="bg-gray-100 px-1 py-0.5 rounded">&lt;your-holmesgpt-url&gt;</code> with your HolmesGPT deployment URL.</p>
              </div>

              {/* Authentication details */}
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 space-y-3">
                <p className="text-sm font-semibold text-blue-900">How authentication works</p>
                <p className="text-sm text-blue-800">
                  HolmesGPT uses a <strong>static bearer token</strong>. Salesforce (or your Flow HTTP callout) must include the token in the <code className="bg-blue-100 px-1 py-0.5 rounded text-xs">X-Salesforce-Token</code> header on every request. HolmesGPT validates the token using a timing-safe comparison and rejects any request with a missing or incorrect token.
                </p>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">Header sent by Salesforce</p>
                  <pre className="bg-white border border-blue-200 rounded px-3 py-2 text-xs text-gray-700 overflow-x-auto">X-Salesforce-Token: &lt;shared-secret-token&gt;</pre>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wider">Admin setup (k8s secret)</p>
                  <pre className="bg-white border border-blue-200 rounded px-3 py-2 text-xs text-gray-700 overflow-x-auto">{`kubectl patch secret holmes-api-keys -n holmesgpt \\\n  --type=merge -p '{"stringData":{"SALESFORCE_WEBHOOK_TOKEN":"<token>"}}'`}</pre>
                </div>
                <p className="text-xs text-blue-600">Authentication is enforced when <code className="bg-blue-100 px-1 py-0.5 rounded">SALESFORCE_WEBHOOK_TOKEN</code> is set and Development Mode is off. To bypass authentication during testing, enable <strong>Development Mode</strong> in <strong>Settings → Webhooks</strong>.</p>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  )
}
