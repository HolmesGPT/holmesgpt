Build and deploy a new version of HolmesGPT to the PDI dev environment.

Follow the deploy-to-aws skill. Execute these steps in order:

1. Verify AWS access: `aws sts get-caller-identity --profile <AWS_PROFILE>`

2. Build and push the Docker image:
```bash
ECR_REGISTRY="<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com"
aws ecr get-login-password --region us-east-1 --profile <AWS_PROFILE> \
  | docker login --username AWS --password-stdin $ECR_REGISTRY
docker build -f infra/Dockerfile.frontend -t $ECR_REGISTRY/holmesgpt:latest .
docker push $ECR_REGISTRY/holmesgpt:latest
```

3. Apply OpenTofu (use `~/.local/bin/tofu`, NOT `terraform`).

   **All secrets are read from AWS Secrets Manager** (single source of truth).
   The secret names follow the pattern `holmesgpt-dev/<name>`.
   On Windows use PowerShell to parse JSON; on Linux/Mac use jq or python3.

```bash
cd infra
PROFILE="pdi-platform-dev"
REGION="us-east-1"

# Helper: read a JSON secret from Secrets Manager and extract a key
# Usage: sm_get <secret-name> <json-key>
sm_get() {
  local raw=$(aws secretsmanager get-secret-value \
    --secret-id "holmesgpt-dev/$1" \
    --region "$REGION" --profile "$PROFILE" \
    --query SecretString --output text 2>/dev/null)
  if [ -z "$raw" ]; then echo ""; return; fi
  # Windows (Git Bash) — use PowerShell for JSON parsing
  powershell -Command "\$s = '$raw'; (\$s | ConvertFrom-Json).'$2'"
}

# ── Read all secrets from Secrets Manager ────────────────────────────
ANTHROPIC_API_KEY=$(sm_get "anthropic-api-key" "ANTHROPIC_API_KEY")

MCP_ADO=$(sm_get "mcp-api-keys" "MCP_ADO_API_KEY")
MCP_ATLASSIAN=$(sm_get "mcp-api-keys" "MCP_ATLASSIAN_API_KEY")
MCP_SALESFORCE=$(sm_get "mcp-api-keys" "MCP_SALESFORCE_API_KEY")
MCP_JENKINS=$(sm_get "mcp-api-keys" "MCP_JENKINS_API_KEY")

# ── Apply ────────────────────────────────────────────────────────────
~/.local/bin/tofu apply -var-file=envs/dev.tfvars \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="mcp_ado_api_key=$MCP_ADO" \
  -var="mcp_atlassian_api_key=$MCP_ATLASSIAN" \
  -var="mcp_salesforce_api_key=$MCP_SALESFORCE" \
  -var="mcp_jenkins_api_key=$MCP_JENKINS" \
  -auto-approve
```

**Note:** Secrets for PagerDuty, Datadog, ADO webhook, Salesforce webhook, Grafana, and
UI credentials are also in Secrets Manager (`holmesgpt-dev/pagerduty`, etc.) but are
currently passed via tfvars variables or set directly in the K8s secret. These will be
migrated to the SM-first pattern as part of the secrets-from-ARN architecture work
(see `docs/superpowers/specs/2026-03-23-secrets-and-ui-config-architecture.md`).

This also creates/updates the DynamoDB table `holmesgpt-dev-config` (defined in `infra/dynamodb.tf`) which stores Projects and LLM instruction overrides. The table name is injected into the pod as `HOLMES_DYNAMODB_TABLE`.

4. Verify the deployment:
```bash
aws eks update-kubeconfig --name holmesgpt-dev --profile <AWS_PROFILE> --region us-east-1
kubectl rollout status deployment/holmes-holmes -n holmesgpt --timeout=120s
curl -s https://<HOLMESGPT_APP_URL>/healthz
curl -s https://<HOLMESGPT_APP_URL>/readyz
```

5. Report the final status to the user including pod state and health check results.
