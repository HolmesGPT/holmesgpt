#!/bin/bash
# HolmesGPT AWS Account Connection Script
# Creates a scoped read-only IAM role that HolmesGPT can assume from the platform account.
set -euo pipefail

ROLE_NAME="HolmesReadOnly"

# ── Prompt for the HolmesGPT platform IRSA role ARN ─────────────────────────
if [ -z "${HOLMES_MCP_ROLE_ARN:-}" ]; then
  echo "Enter the HolmesGPT AWS MCP IRSA role ARN from the platform account"
  echo "(e.g. arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp):"
  read -r HOLMES_MCP_ROLE_ARN
fi

if [[ ! "$HOLMES_MCP_ROLE_ARN" =~ ^arn:aws:iam:: ]]; then
  echo "ERROR: Invalid ARN format. Expected arn:aws:iam::<account-id>:role/<role-name>"
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Setting up HolmesGPT role in account: $ACCOUNT_ID"

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
    }
  ]
}
EOF
)

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document "$TRUST_POLICY" \
  --description "HolmesGPT read-only investigation role" \
  --tags Key=ManagedBy,Value=holmesgpt-script Key=Application,Value=holmesgpt 2>/dev/null \
  || aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST_POLICY"

# ── Scoped read-only policy (matches infra/logistics-cross-account/main.tf) ──
POLICY_NAME="${ROLE_NAME}-triage"
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
    }
  ]
}
POLICYEOF
)

# Create or update the custom policy
EXISTING_POLICY_ARN=$(aws iam list-policies --scope Local --query "Policies[?PolicyName=='${POLICY_NAME}'].Arn" --output text 2>/dev/null || true)
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
