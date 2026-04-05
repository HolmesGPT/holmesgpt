# HolmesGPT Cross-Account Role for Logistics Accounts
#
# Deploy this module into each logistics AWS account (ci, dev, stage, prod, sandbox).
# It creates a read-only IAM role that the HolmesGPT AWS MCP server can assume
# from the platform account to investigate incidents.
#
# Usage:
#   cd infra/logistics-cross-account
#   terraform init
#   terraform apply \
#     -var="aws_profile=<LOGISTICS_AWS_PROFILE>" \
#     -var="holmes_mcp_role_arn=arn:aws:iam::<PLATFORM_ACCOUNT_ID>:role/holmesgpt-dev-aws-mcp" \
#     -var="eks_oidc_provider_arn=arn:aws:iam::<PLATFORM_ACCOUNT_ID>:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/<OIDC_ID>" \
#     -var="eks_oidc_provider_url=oidc.eks.us-east-1.amazonaws.com/id/<OIDC_ID>"
#
# Repeat for each account, changing aws_profile each time.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# ── Variables ────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile for the target logistics account"
  type        = string
}

variable "holmes_mcp_role_arn" {
  description = "ARN of the HolmesGPT AWS MCP IRSA role in the platform account (allowed to assume this role)"
  type        = string
  # Example: arn:aws:iam::<PLATFORM_ACCOUNT_ID>:role/holmesgpt-dev-aws-mcp
}

variable "eks_oidc_provider_url" {
  description = "URL of the EKS OIDC provider (without https://), e.g. oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE"
  type        = string
  default     = ""
}

variable "eks_service_account" {
  description = "Kubernetes service account that will assume this role (namespace:serviceaccount)"
  type        = string
  default     = "holmesgpt:aws-api-mcp-sa"
}

variable "role_name" {
  description = "Name of the IAM role to create in this account"
  type        = string
  default     = "HolmesReadOnly"
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    ManagedBy   = "terraform"
    Application = "holmesgpt"
    Purpose     = "incident-triage"
  }
}

# ── Data ─────────────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

# ── OIDC Provider ─────────────────────────────────────────────────────────────
# Register the platform account's EKS OIDC provider in this account so that
# the AWS MCP server pod can call AssumeRoleWithWebIdentity directly.

resource "aws_iam_openid_connect_provider" "eks" {
  count = var.eks_oidc_provider_url != "" ? 1 : 0

  url             = "https://${var.eks_oidc_provider_url}"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["06b25927c42a721631c1efd9431e648fa62e1e39"]
  tags            = var.tags
}

# ── IAM Role ─────────────────────────────────────────────────────────────────

resource "aws_iam_role" "holmes_readonly" {
  name        = var.role_name
  description = "Read-only role for HolmesGPT incident triage from the platform account"
  tags        = var.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "AllowHolmesMCPAssumeRole"
          Effect = "Allow"
          Principal = {
            AWS = var.holmes_mcp_role_arn
          }
          Action = "sts:AssumeRole"
        }
      ],
      var.eks_oidc_provider_url != "" ? [
        {
          Sid    = "AllowHolmesMCPWebIdentity"
          Effect = "Allow"
          Principal = {
            # Use the OIDC provider registered in THIS account (not the platform account)
            Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${var.eks_oidc_provider_url}"
          }
          Action = "sts:AssumeRoleWithWebIdentity"
          Condition = {
            StringEquals = {
              "${var.eks_oidc_provider_url}:aud" = "sts.amazonaws.com"
              "${var.eks_oidc_provider_url}:sub" = "system:serviceaccount:${var.eks_service_account}"
            }
          }
        }
      ] : []
    )
  })
}

# Scoped read-only policy for incident triage.
# Only the specific actions needed for observability and triage are granted.
# No mutating actions (Create, Delete, Update, Put, Modify, Run, etc.) are included.
resource "aws_iam_policy" "holmes_triage" {
  name        = "${var.role_name}-triage"
  description = "Scoped read-only permissions for HolmesGPT incident triage"
  tags        = var.tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ── Compute ──────────────────────────────────────────────────────────
      {
        Sid    = "EC2Read"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "ec2:GetConsoleOutput",
          "ec2:GetConsoleScreenshot",
          "autoscaling:Describe*",
          "elasticloadbalancing:Describe*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSRead"
        Effect = "Allow"
        Action = [
          "ecs:Describe*",
          "ecs:List*"
        ]
        Resource = "*"
      },
      {
        Sid    = "EKSRead"
        Effect = "Allow"
        Action = [
          "eks:Describe*",
          "eks:List*",
          "eks:AccessKubernetesApi"
        ]
        Resource = "*"
      },
      {
        Sid    = "LambdaRead"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:GetFunctionEventInvokeConfig",
          "lambda:GetPolicy",
          "lambda:ListFunctions",
          "lambda:ListAliases",
          "lambda:ListEventSourceMappings",
          "lambda:ListVersionsByFunction"
        ]
        Resource = "*"
      },
      # ── Storage ──────────────────────────────────────────────────────────
      {
        Sid    = "S3Read"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning",
          "s3:GetBucketTagging",
          "s3:GetBucketPolicy",
          "s3:GetBucketAcl",
          "s3:GetBucketLogging",
          "s3:GetBucketNotification",
          "s3:GetEncryptionConfiguration",
          "s3:GetLifecycleConfiguration",
          "s3:ListAllMyBuckets",
          "s3:ListBucket",
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetObjectTagging"
        ]
        Resource = "*"
      },
      # ── Database ─────────────────────────────────────────────────────────
      {
        Sid    = "RDSRead"
        Effect = "Allow"
        Action = [
          "rds:Describe*",
          "rds:ListTagsForResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeTable",
          "dynamodb:DescribeContinuousBackups",
          "dynamodb:DescribeTimeToLive",
          "dynamodb:DescribeGlobalTable",
          "dynamodb:ListTables",
          "dynamodb:ListTagsOfResource",
          "dynamodb:GetItem",
          "dynamodb:BatchGetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:DescribeStream",
          "dynamodb:ListStreams"
        ]
        Resource = "*"
      },
      # ── Observability ────────────────────────────────────────────────────
      {
        Sid    = "CloudWatchRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:DescribeAlarms",
          "cloudwatch:DescribeAlarmHistory",
          "cloudwatch:DescribeAnomalyDetectors",
          "cloudwatch:GetDashboard",
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricWidgetImage",
          "cloudwatch:ListDashboards",
          "cloudwatch:ListMetrics",
          "cloudwatch:ListTagsForResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsRead"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:DescribeMetricFilters",
          "logs:DescribeSubscriptionFilters",
          "logs:FilterLogEvents",
          "logs:GetLogEvents",
          "logs:GetLogGroupFields",
          "logs:GetLogRecord",
          "logs:GetQueryResults",
          "logs:ListTagsLogGroup",
          "logs:StartQuery",
          "logs:StopQuery"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudTrailRead"
        Effect = "Allow"
        Action = [
          "cloudtrail:DescribeTrails",
          "cloudtrail:GetEventSelectors",
          "cloudtrail:GetTrailStatus",
          "cloudtrail:ListTrails",
          "cloudtrail:LookupEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "XRayRead"
        Effect = "Allow"
        Action = [
          "xray:BatchGetTraces",
          "xray:GetGroups",
          "xray:GetSamplingRules",
          "xray:GetServiceGraph",
          "xray:GetTraceSummaries"
        ]
        Resource = "*"
      },
      # ── Networking ───────────────────────────────────────────────────────
      {
        Sid    = "Route53Read"
        Effect = "Allow"
        Action = [
          "route53:GetHostedZone",
          "route53:GetHealthCheck",
          "route53:ListHostedZones",
          "route53:ListResourceRecordSets",
          "route53:ListHealthChecks",
          "route53:ListTagsForResource"
        ]
        Resource = "*"
      },
      # ── IAM (read-only, for context) ─────────────────────────────────────
      {
        Sid    = "IAMRead"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListAttachedRolePolicies",
          "iam:ListRolePolicies",
          "iam:ListRoles",
          "iam:ListPolicies"
        ]
        Resource = "*"
      },
      # ── Resource tagging ─────────────────────────────────────────────────
      {
        Sid    = "TaggingRead"
        Effect = "Allow"
        Action = [
          "tag:GetResources",
          "tag:GetTagKeys",
          "tag:GetTagValues"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "triage" {
  role       = aws_iam_role.holmes_readonly.name
  policy_arn = aws_iam_policy.holmes_triage.arn
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "role_arn" {
  description = "ARN of the HolmesReadOnly role — add this to logistics_accounts in dev.tfvars"
  value       = aws_iam_role.holmes_readonly.arn
}

output "account_id" {
  description = "AWS account ID of this logistics account"
  value       = data.aws_caller_identity.current.account_id
}

output "tfvars_snippet" {
  description = "Paste this into the logistics_accounts block in infra/envs/dev.tfvars"
  value       = <<-EOT
    # Add to logistics_accounts in infra/envs/dev.tfvars:
    "<profile-name>" = {
      account_id = "${data.aws_caller_identity.current.account_id}"
      role_arn   = "${aws_iam_role.holmes_readonly.arn}"
      region     = "${var.aws_region}"
    }
  EOT
}
