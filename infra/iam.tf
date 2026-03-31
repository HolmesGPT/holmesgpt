# IRSA role for Holmes pods - allows reading secrets from Secrets Manager
module "holmes_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${local.cluster_name}-holmes"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["holmesgpt:holmes-holmes-service-account"]
    }
  }

  role_policy_arns = {
    secrets  = aws_iam_policy.holmes_secrets.arn
    dynamodb = aws_iam_policy.holmes_dynamodb.arn
  }
}

resource "aws_iam_policy" "holmes_secrets" {
  name        = "${local.cluster_name}-holmes-secrets"
  description = "Allow Holmes to read API keys from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = compact([
          aws_secretsmanager_secret.anthropic_api_key.arn,
          aws_secretsmanager_secret.mcp_api_keys.arn,
          aws_secretsmanager_secret.holmes_ui_credentials.arn,
          aws_secretsmanager_secret.grafana.arn,
          aws_secretsmanager_secret.datadog.arn,
          aws_secretsmanager_secret.pagerduty.arn,
          aws_secretsmanager_secret.ado_webhook.arn,
          aws_secretsmanager_secret.salesforce_webhook.arn,
          "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${local.cluster_name}/project-*",
          # DBADash credentials — global + per-project secrets fetched at runtime via boto3
          var.dbdash_secrets_manager_arn != "" ? var.dbdash_secrets_manager_arn : "",
          "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${local.cluster_name}/dbdash-*",
        ])
      }
    ]
  })
}

resource "aws_iam_policy" "holmes_dynamodb" {
  name        = "${local.cluster_name}-holmes-dynamodb"
  description = "Allow Holmes to read/write the config DynamoDB table"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = aws_dynamodb_table.holmes_config.arn
      }
    ]
  })
}

# IRSA role for the AWS MCP server pod - allows assuming roles in logistics accounts
# Only created when aws_mcp_enabled = true (i.e., real account IDs are in logistics_accounts)
locals {
  logistics_role_arns = [for _, cfg in var.logistics_accounts : cfg.role_arn]
}

module "aws_mcp_irsa" {
  count   = var.aws_mcp_enabled ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${local.cluster_name}-aws-mcp"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["holmesgpt:aws-api-mcp-sa"]
    }
  }

  role_policy_arns = {
    cross_account = aws_iam_policy.aws_mcp_cross_account[0].arn
  }
}

resource "aws_iam_policy" "aws_mcp_cross_account" {
  count       = var.aws_mcp_enabled ? 1 : 0
  name        = "${local.cluster_name}-aws-mcp-cross-account"
  description = "Allow AWS MCP server to assume roles in logistics accounts"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = local.logistics_role_arns
      }
    ]
  })
}

# IRSA role for AWS Load Balancer Controller
module "alb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${local.cluster_name}-alb-controller"

  attach_load_balancer_controller_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }
}
