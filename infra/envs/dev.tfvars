# Dev environment - pdi-platform-dev (account 717423812395)

aws_profile = "pdi-platform-dev"
aws_region  = "us-east-1"
environment = "dev"

# Networking - platform_dev_us-east-1_vpc
vpc_id = "vpc-03d8d8f4fb1f915c5"

private_subnet_ids = [
  "subnet-0175f2446e10155c8", # platform-dev-private-services-01-us-east-1a
  "subnet-09fa7974f71f07c2a", # platform-dev-private-services-01-us-east-1b
]

public_subnet_ids = [
  "subnet-08196139ac2b08687", # platform-dev-public-ext01-us-east-1a
  "subnet-00cd06ad109a88780", # platform-dev-public-ext01-us-east-1b
]

# EKS
eks_cluster_version = "1.32"
node_instance_type  = "t3.medium"
node_min_size       = 1
node_max_size       = 2
node_desired_size   = 1

# DNS & TLS
route53_zone_id     = "Z09344573OY2RCX1Q76DP"
route53_zone_name   = "dev.platform.pditechnologies.com"
acm_certificate_arn = "arn:aws:acm:us-east-1:717423812395:certificate/c99bb8a8-6506-487e-a628-5a084c9ef69c"
hostname            = "holmesgpt.dev.platform.pditechnologies.com"

# LLM
anthropic_api_base = "https://ai-gateway.platform.pditechnologies.com"
anthropic_api_key  = "" # Set via TF_VAR_anthropic_api_key or -var flag
holmes_model       = "anthropic/claude-opus-4-6"

# Holmes
holmes_replicas  = 1
holmes_image_tag = "latest"

# UI Auth — Okta OIDC (PKCE)
okta_issuer              = "https://pdisoftware.okta.com/oauth2/default"
okta_client_id           = "0oa1ae04lowCIDE9B2p8"
holmes_super_admin_email = "srinivasreddy.v@pditechnologies.com"
okta_group_id             = "00g1ae0b43fuHGUXw2p8"
okta_api_token            = "" # Read from Secrets Manager via okta_api_token_secret_arn
okta_api_token_secret_arn = "arn:aws:secretsmanager:us-east-1:717423812395:secret:holmesgpt-dev/okta-api-token-32cDY1"

# MCP Integration API Keys
mcp_ado_api_key        = "" # Set via TF_VAR_mcp_ado_api_key or -var flag
mcp_atlassian_api_key  = "" # Set via TF_VAR_mcp_atlassian_api_key or -var flag
mcp_salesforce_api_key = "" # Set via TF_VAR_mcp_salesforce_api_key or -var flag

# Tags
tags = {
  Team        = "Tool COE"
  CostCenter  = "Cloud Engineering"
  Application = "holmesgpt"
}

# Logistics cross-account access
# HolmesReadOnly roles are deployed via infra/logistics-cross-account/ into each account.
# prod is intentionally excluded.
logistics_accounts = {
  logistics-ci = {
    account_id = "229743609213"
    role_arn   = "arn:aws:iam::229743609213:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-dev = {
    account_id = "690917928966"
    role_arn   = "arn:aws:iam::690917928966:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-stage = {
    account_id = "178396448338"
    role_arn   = "arn:aws:iam::178396448338:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-sandbox = {
    account_id = "087983023125"
    role_arn   = "arn:aws:iam::087983023125:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-prod = {
    account_id = "342706430250"
    role_arn   = "arn:aws:iam::342706430250:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-dev = {
    account_id = "689863073433"
    role_arn   = "arn:aws:iam::689863073433:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-prod = {
    account_id = "803964703583"
    role_arn   = "arn:aws:iam::803964703583:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-stage = {
    account_id = "415641701024"
    role_arn   = "arn:aws:iam::415641701024:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
}

# Enable the AWS MCP server addon now that real account IDs are set
aws_mcp_enabled = true



# ADO Webhook — set via TF_VAR_ environment variables, not here (secrets!)
# TF_VAR_ado_pat, TF_VAR_ado_organization, TF_VAR_ado_webhook_username, TF_VAR_ado_webhook_password
ado_organization       = "pdidev"
ado_webhook_username   = "holmesgpt"


# DBADash Web integration — credentials stored in Secrets Manager (fetched at runtime by the pod)
dbdash_api_url             = "https://db-monitor.shared.platform.pditechnologies.com"
dbdash_secrets_manager_arn = "arn:aws:secretsmanager:us-east-1:717423812395:secret:holmesgpt-dev/dbdash-web-credentials-g3D5at"