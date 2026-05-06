# Atlassian (MCP)

Query Jira issues and Confluence pages via the PDI-hosted Atlassian MCP server.

## Capabilities

- Jira: search issues with JQL, fetch issue details, comments, transitions.
- Confluence: search pages with CQL, fetch page content, page history.
- Runbook discovery: cross-reference Jira incidents with Confluence runbooks.

## Configuration

HolmesGPT supports two modes:

### 1. Global (env-var fallback)

When `MCP_ATLASSIAN_API_KEY` is populated in the pod environment (via `holmesgpt-<env>/mcp-api-keys` in Secrets Manager), the `atlassian` toolset is auto-registered and shared across every project that doesn't have a per-project instance.

To populate the key in dev or prod, run:

```bash
bash scripts/populate_mcp_keys.sh dev      # or: prod
```

This reads from the source secret (`mcp-readonly-api-keys-L63NWI` in account `717423812395`) and writes to `holmesgpt-<env>/mcp-api-keys`.

### 2. Per-project instance

Store a per-project API key in AWS Secrets Manager as JSON:

```json
{ "api_key": "<atlassian-mcp-api-key>" }
```

In the HolmesGPT UI:

1. Go to **Instances → New Instance**.
2. Pick type `atlassian` and name it (e.g. `atlassian-acme`).
3. Set **Secret ARN** to the Secrets Manager secret above.
4. Leave **MCP URL** empty to use the default PDI gateway URL (or override for a different gateway).
5. Click **Test Connection** to verify.
6. Tag the instance (e.g. `project=acme`) so the project picks it up via tag matching.

## Common Queries

```
"Find open Jira incidents tagged for the checkout-api service"
"Search Confluence for a runbook on payment service outages"
"Get the comments on PROJ-1234 to see what the previous responder tried"
```

## Troubleshooting

```bash
# Verify the key is populated in the right env
aws secretsmanager get-secret-value --secret-id holmesgpt-dev/mcp-api-keys \
  --profile pdi-platform-dev --region us-east-1 --query SecretString --output text

# Test the connection end-to-end via the UI → Test Connection button.
# Expected: {"ok": true, "status": "success", "tool_count": N} where N > 0.
```

| Symptom | Likely cause |
|---|---|
| `tool_count: 0` on success | Gateway reachable but no tools registered for this key — check gateway-side config. |
| `HTTP 401` on Test Connection | Key is invalid or expired — re-run `populate_mcp_keys.sh`. |
| No Atlassian tools in chat | No project-level instance AND global env var is empty — populate the secret. |
| Query returns empty results | Check the key's scope in the PDI gateway — it may be restricted to specific projects. |
