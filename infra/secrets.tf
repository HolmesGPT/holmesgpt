resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${local.cluster_name}/anthropic-api-key"
  description             = "Anthropic API key for HolmesGPT"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  secret_id = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = jsonencode({
    ANTHROPIC_API_KEY  = var.anthropic_api_key
    ANTHROPIC_API_BASE = var.anthropic_api_base
  })
}

# MCP API Keys secret — groups all three MCP keys in one secret
resource "aws_secretsmanager_secret" "mcp_api_keys" {
  name                    = "${local.cluster_name}/mcp-api-keys"
  description             = "API keys for MCP integrations (ADO, Atlassian, Salesforce, Jenkins, AWS Billing & Cost Management)"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "mcp_api_keys" {
  secret_id = aws_secretsmanager_secret.mcp_api_keys.id
  secret_string = jsonencode({
    MCP_ADO_API_KEY                         = var.mcp_ado_api_key
    MCP_ATLASSIAN_API_KEY                   = var.mcp_atlassian_api_key
    MCP_SALESFORCE_API_KEY                  = var.mcp_salesforce_api_key
    MCP_JENKINS_API_KEY                     = var.mcp_jenkins_api_key
    MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY = var.mcp_aws_billing_cost_management_api_key
  })
}

# Grafana token secret
resource "aws_secretsmanager_secret" "grafana" {
  name                    = "${local.cluster_name}/grafana"
  description             = "Grafana service account token for HolmesGPT"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "grafana" {
  secret_id = aws_secretsmanager_secret.grafana.id
  secret_string = jsonencode({
    GRAFANA_API_KEY = var.grafana_api_key
    GRAFANA_URL     = var.grafana_url
  })
}

data "aws_secretsmanager_secret_version" "grafana" {
  secret_id  = aws_secretsmanager_secret.grafana.id
  depends_on = [aws_secretsmanager_secret_version.grafana]
}

# Okta auth config secret
resource "aws_secretsmanager_secret" "holmes_okta_config" {
  name                    = "${local.cluster_name}/holmes-okta-config"
  description             = "Okta OIDC configuration for Holmes UI"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

# Read the manually-created Okta API token secret
data "aws_secretsmanager_secret_version" "okta_api_token" {
  count     = var.okta_api_token_secret_arn != "" ? 1 : 0
  secret_id = var.okta_api_token_secret_arn
}

locals {
  okta_api_token_from_sm = (
    var.okta_api_token_secret_arn != ""
    ? jsondecode(data.aws_secretsmanager_secret_version.okta_api_token[0].secret_string)["OKTA_API_TOKEN"]
    : var.okta_api_token
  )
}

resource "aws_secretsmanager_secret_version" "holmes_okta_config" {
  secret_id = aws_secretsmanager_secret.holmes_okta_config.id
  secret_string = jsonencode({
    OKTA_ISSUER              = var.okta_issuer
    OKTA_CLIENT_ID           = var.okta_client_id
    HOLMES_SUPER_ADMIN_EMAIL = var.holmes_super_admin_email
    OKTA_API_TOKEN           = local.okta_api_token_from_sm
    OKTA_GROUP_ID            = var.okta_group_id
  })
}

# Data sources to read back secret values at plan/apply time
data "aws_secretsmanager_secret_version" "mcp_api_keys" {
  secret_id  = aws_secretsmanager_secret.mcp_api_keys.id
  depends_on = [aws_secretsmanager_secret_version.mcp_api_keys]
}

data "aws_secretsmanager_secret_version" "holmes_okta_config" {
  secret_id  = aws_secretsmanager_secret.holmes_okta_config.id
  depends_on = [aws_secretsmanager_secret_version.holmes_okta_config]
}

# Datadog secret — managed by OpenTofu; values set via variables (default empty)
resource "aws_secretsmanager_secret" "datadog" {
  name                    = "${local.cluster_name}/datadog"
  description             = "Datadog API credentials for HolmesGPT"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "datadog" {
  secret_id = aws_secretsmanager_secret.datadog.id
  secret_string = jsonencode({
    DATADOG_API_KEY = var.datadog_api_key
    DATADOG_APP_KEY = var.datadog_app_key
    DATADOG_API_URL = var.datadog_api_url
  })
}

data "aws_secretsmanager_secret_version" "datadog" {
  secret_id  = aws_secretsmanager_secret.datadog.id
  depends_on = [aws_secretsmanager_secret_version.datadog]
}

# PagerDuty secret — managed by OpenTofu; values set via variables (default empty)
resource "aws_secretsmanager_secret" "pagerduty" {
  name                    = "${local.cluster_name}/pagerduty"
  description             = "PagerDuty credentials for HolmesGPT"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "pagerduty" {
  secret_id = aws_secretsmanager_secret.pagerduty.id
  secret_string = jsonencode({
    PAGERDUTY_API_KEY        = var.pagerduty_api_key
    PAGERDUTY_USER_EMAIL     = var.pagerduty_user_email
    PAGERDUTY_WEBHOOK_SECRET = var.pagerduty_webhook_secret
  })
}

data "aws_secretsmanager_secret_version" "pagerduty" {
  secret_id  = aws_secretsmanager_secret.pagerduty.id
  depends_on = [aws_secretsmanager_secret_version.pagerduty]
}

# ADO webhook secret — managed by OpenTofu; values set via variables (default empty)
resource "aws_secretsmanager_secret" "ado_webhook" {
  name                    = "${local.cluster_name}/ado-webhook"
  description             = "Azure DevOps webhook credentials for HolmesGPT"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "ado_webhook" {
  secret_id = aws_secretsmanager_secret.ado_webhook.id
  secret_string = jsonencode({
    ADO_WEBHOOK_USERNAME = var.ado_webhook_username
    ADO_WEBHOOK_PASSWORD = var.ado_webhook_password
    ADO_PAT              = var.ado_pat
    ADO_ORGANIZATION     = var.ado_organization
  })
}

data "aws_secretsmanager_secret_version" "ado_webhook" {
  secret_id  = aws_secretsmanager_secret.ado_webhook.id
  depends_on = [aws_secretsmanager_secret_version.ado_webhook]
}

# Salesforce webhook secret — managed by OpenTofu; values set via variables (default empty)
resource "aws_secretsmanager_secret" "salesforce_webhook" {
  name                    = "${local.cluster_name}/salesforce-webhook"
  description             = "Salesforce webhook credentials for HolmesGPT"
  recovery_window_in_days = var.environment == "dev" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "salesforce_webhook" {
  secret_id = aws_secretsmanager_secret.salesforce_webhook.id
  secret_string = jsonencode({
    SALESFORCE_WEBHOOK_TOKEN = var.salesforce_webhook_token
    SALESFORCE_INSTANCE_URL  = var.salesforce_instance_url
    SALESFORCE_ACCESS_TOKEN  = var.salesforce_access_token
  })
}

data "aws_secretsmanager_secret_version" "salesforce_webhook" {
  secret_id  = aws_secretsmanager_secret.salesforce_webhook.id
  depends_on = [aws_secretsmanager_secret_version.salesforce_webhook]
}
