# WAF v2 Web ACL for rate limiting and security
resource "aws_wafv2_web_acl" "api_rate_limit" {
  name        = "${local.cluster_name}-api-rate-limit"
  description = "Rate limit /api/v1/ endpoints and block known exploits"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # AWS Managed Rule: Known Bad Inputs (includes Log4j / CVE-2021-44228 protection)
  rule {
    name     = "aws-known-bad-inputs"
    priority = 0

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.cluster_name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Rate limit by IP: 100 requests per 5 minutes on /api/v1/
  rule {
    name     = "api-v1-ip-rate-limit"
    priority = 1

    action {
      block {
        custom_response {
          response_code = 429
        }
      }
    }

    statement {
      rate_based_statement {
        limit              = 100
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/v1/"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.cluster_name}-api-v1-ip-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.cluster_name}-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name        = "${local.cluster_name}-api-rate-limit"
    Environment = var.environment
  }
}
