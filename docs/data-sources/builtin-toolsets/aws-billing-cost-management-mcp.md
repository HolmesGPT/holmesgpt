# AWS Billing & Cost Management (MCP)

Query AWS billing, cost, usage, and pricing data via the PDI-hosted AWS Billing & Cost Management MCP server (a bridge over the AWS Labs `billing-cost-management-mcp-server`). All access is **read-only** and may lag real time by up to ~24 hours.

## Capabilities

- Cost & usage over time (Cost Explorer): spend by service, account, region, tag, or usage type.
- Cost forecasts and period-over-period comparisons.
- Budgets and budget status.
- Savings Plans / Reserved Instance utilization, coverage, and recommendations.
- Compute Optimizer right-sizing recommendations and Cost Optimization Hub savings.
- Free Tier usage, Storage Lens summaries, AWS pricing lookups, billing views, and account associations.

## Configuration

Same pattern as [Atlassian (MCP)](atlassian-mcp.md). Populate `MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY` in the env-level secret via:

```bash
bash scripts/populate_mcp_keys.sh dev      # or: prod
```

Or create a per-project instance in the UI with `type: aws-billing-cost-management` and a per-project Secrets Manager ARN.

For local development, add it to `~/.holmes/config.yaml`:

```yaml
mcp_servers:
  aws-billing-cost-management:
    description: "AWS Billing & Cost Management - cost & usage, pricing, budgets, billing views, Compute Optimizer"
    config:
      mode: streamable-http
      url: https://mcp-api.platform.pditechnologies.com/v1/aws-billing-cost-management-sse/mcp
      headers:
        x-api-key: "{{ env.MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY }}"
```

## Common Queries

```
"What did we spend on Amazon EC2 last month, broken down by region?"
"Show the daily unblended cost for the last 14 days grouped by service"
"Why did our AWS bill spike on the 3rd? Drill into the top service."
"What Compute Optimizer right-sizing recommendations do we have for EC2?"
"How are our Savings Plans being utilized this month?"
```

## Troubleshooting

```bash
# Verify MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY is set
aws secretsmanager get-secret-value --secret-id holmesgpt-dev/mcp-api-keys \
  --profile pdi-platform-dev --region us-east-1 --query SecretString --output text \
  | python -c "import json,sys; d=json.loads(sys.stdin.read()); print('MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY set:', bool(d.get('MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY','')))"
```

| Symptom | Likely cause |
|---|---|
| `tool_count: 0` on success | Gateway reachable but no tools exposed for this key. |
| `HTTP 401` | Key invalid or expired. |
| `AccessDeniedException` on some tools | The payer-account read-only role is missing IAM permissions for that billing/cost API. |
| `No credential source` | Instance has no `secret_arn` and `MCP_AWS_BILLING_COST_MANAGEMENT_API_KEY` is empty. |
