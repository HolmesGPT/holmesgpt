# HolmesGPT AWS Account Connection Script (PowerShell)
# Creates a scoped read-only IAM role that HolmesGPT can assume from the platform account.
$ErrorActionPreference = "Stop"

$RoleName = "HolmesReadOnly"

# ── Prompt for the HolmesGPT platform IRSA role ARN ─────────────────────────
if (-not $env:HOLMES_MCP_ROLE_ARN) {
    $HolmesMcpRoleArn = Read-Host "Enter the HolmesGPT AWS MCP IRSA role ARN from the platform account`n(e.g. arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp)"
} else {
    $HolmesMcpRoleArn = $env:HOLMES_MCP_ROLE_ARN
}

if ($HolmesMcpRoleArn -notmatch "^arn:aws:iam::") {
    Write-Error "Invalid ARN format. Expected arn:aws:iam::<account-id>:role/<role-name>"
    exit 1
}

$AccountId = (aws sts get-caller-identity --query Account --output text)
Write-Host "Setting up HolmesGPT role in account: $AccountId"

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
    {"Sid":"TaggingRead","Effect":"Allow","Action":["tag:GetResources","tag:GetTagKeys","tag:GetTagValues"],"Resource":"*"}
  ]
}
'@

# Create or update the custom policy
$ExistingPolicyArn = (aws iam list-policies --scope Local --query "Policies[?PolicyName=='$PolicyName'].Arn" --output text 2>$null)
if ($ExistingPolicyArn -and $ExistingPolicyArn -ne "None") {
    $OldVersions = (aws iam list-policy-versions --policy-arn $ExistingPolicyArn --query "Versions[?!IsDefaultVersion].VersionId" --output text) -split "\s+"
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
