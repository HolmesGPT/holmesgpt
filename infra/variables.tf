variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile name"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, prod)"
  type        = string
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "holmesgpt"
}

# Networking
variable "vpc_id" {
  description = "Existing VPC ID"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for EKS nodes"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for ALB"
  type        = list(string)
}

# EKS
variable "eks_cluster_version" {
  description = "EKS Kubernetes version"
  type        = string
  default     = "1.32"
}

variable "node_instance_type" {
  description = "EC2 instance type for EKS managed node group"
  type        = string
  default     = "t3.medium"
}

variable "node_min_size" {
  description = "Minimum number of nodes"
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of nodes"
  type        = number
  default     = 3
}

variable "node_desired_size" {
  description = "Desired number of nodes"
  type        = number
  default     = 1
}

# DNS & TLS
variable "route53_zone_id" {
  description = "Route53 hosted zone ID"
  type        = string
}

variable "route53_zone_name" {
  description = "Route53 hosted zone domain name"
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for HTTPS"
  type        = string
}

variable "hostname" {
  description = "Hostname for Holmes (e.g., holmesgpt.dev.example.com)"
  type        = string
}

# LLM Configuration
variable "anthropic_api_base" {
  description = "Anthropic API base URL"
  type        = string
  default     = "https://<LLM_GATEWAY_URL>"
}

variable "anthropic_api_key" {
  description = "Anthropic API key (stored in Secrets Manager)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "holmes_model" {
  description = "LLM model to use"
  type        = string
  default     = "anthropic/claude-sonnet-4-5-20250929"
}

# Holmes
variable "holmes_replicas" {
  description = "Number of Holmes replicas"
  type        = number
  default     = 1
}

variable "holmes_image_tag" {
  description = "Holmes container image tag"
  type        = string
  default     = "latest"
}

# UI Authentication
variable "holmes_ui_username" {
  description = "Username for Holmes UI login"
  type        = string
  default     = "admin"
}

variable "holmes_ui_password" {
  description = "Password for Holmes UI login"
  type        = string
  sensitive   = true
  default     = ""
}

# MCP Integration API Keys
variable "mcp_ado_api_key" {
  description = "API key for Azure DevOps MCP server"
  type        = string
  sensitive   = true
  default     = ""
}

variable "mcp_atlassian_api_key" {
  description = "API key for Atlassian MCP server"
  type        = string
  sensitive   = true
  default     = ""
}

variable "mcp_salesforce_api_key" {
  description = "API key for Salesforce MCP server"
  type        = string
  sensitive   = true
  default     = ""
}

# Grafana
variable "grafana_url" {
  description = "Grafana instance URL"
  type        = string
  default     = "https://grafana-us.shared.logistics.pdisoftware.com"
}

variable "grafana_api_key" {
  description = "Grafana service account token for HolmesGPT"
  type        = string
  sensitive   = true
  default     = ""
}

# Datadog
variable "datadog_api_key" {
  description = "Datadog API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "datadog_app_key" {
  description = "Datadog application key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "datadog_api_url" {
  description = "Datadog API URL"
  type        = string
  default     = "https://api.datadoghq.com"
}

# PagerDuty
variable "pagerduty_api_key" {
  description = "PagerDuty API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "pagerduty_user_email" {
  description = "PagerDuty user email for API calls"
  type        = string
  default     = ""
}

variable "pagerduty_webhook_secret" {
  description = "PagerDuty webhook secret for signature verification"
  type        = string
  sensitive   = true
  default     = ""
}

# Azure DevOps webhook
variable "ado_webhook_username" {
  description = "Azure DevOps webhook basic auth username"
  type        = string
  default     = ""
}

variable "ado_webhook_password" {
  description = "Azure DevOps webhook basic auth password"
  type        = string
  sensitive   = true
  default     = ""
}

variable "ado_pat" {
  description = "Azure DevOps Personal Access Token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "ado_organization" {
  description = "Azure DevOps organization name"
  type        = string
  default     = ""
}

# Salesforce webhook
variable "salesforce_webhook_token" {
  description = "Salesforce webhook token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "salesforce_instance_url" {
  description = "Salesforce instance URL"
  type        = string
  default     = ""
}

variable "salesforce_access_token" {
  description = "Salesforce access token"
  type        = string
  sensitive   = true
  default     = ""
}

# Tags
variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}

# Logistics cross-account access
# Map of profile name -> { account_id, role_arn, region }
# The AWS MCP server will assume these roles to access each logistics account.
variable "logistics_accounts" {
  description = "Logistics AWS accounts for cross-account access via the AWS MCP server"
  type = map(object({
    account_id = string
    role_arn   = string
    region     = optional(string, "us-east-1")
  }))
  default = {}
}

# Set to true only after filling in real account IDs in logistics_accounts
# and after deploying infra/logistics-cross-account/ into each target account.
variable "aws_mcp_enabled" {
  description = "Enable the AWS MCP server addon (requires real logistics_accounts values)"
  type        = bool
  default     = false
}

# DBADash Web integration
variable "dbdash_api_url" {
  description = "Base URL of the dbdash-web instance"
  type        = string
  default     = "https://db-monitor.shared.platform.pditechnologies.com"
}

variable "dbdash_secrets_manager_arn" {
  description = "ARN of the Secrets Manager secret containing dbdash-web Cognito credentials (username, password, cognito_user_pool_id, cognito_client_id)"
  type        = string
  default     = ""
}
