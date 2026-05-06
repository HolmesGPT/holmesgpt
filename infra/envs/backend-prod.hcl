# Production backend config for OpenTofu.
# Usage: cd infra && tofu init -backend-config=envs/backend-prod.hcl -reconfigure
#
# This file is NOT used in CI — the pdi-iac.yaml workflow injects
# backend config via -backend-config flags at init time.

bucket  = "holmesgpt-tfstate-827852520868"
key     = "holmesgpt/prod/terraform.tfstate"
region  = "us-east-1"
profile = "pdi-platform-all"
encrypt = true
