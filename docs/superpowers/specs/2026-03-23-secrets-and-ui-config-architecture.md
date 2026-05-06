# Secrets from ARN and UI Config Precedence — Design Document

**Date:** 2026-03-23
**Status:** Draft

---

## Issue 1: Secrets from ARN, Not Values

### Current State

TF variables accept raw secret values (`TF_VAR_pagerduty_api_key="actual-key"`), writes them to Secrets Manager, then copies to a K8s secret. Problems:
- tfvars contains plaintext credentials
- Every `tofu apply` overwrites Secrets Manager values
- Rotating a secret requires a TF apply

### Recommended: ARN-Based Lookup with Backwards Compatibility

New variables replace each secret-value variable with an ARN variable:

```hcl
variable "anthropic_secret_arn" {
  description = "ARN of the Secrets Manager secret containing ANTHROPIC_API_KEY"
  type        = string
  default     = ""  # Empty = fall back to legacy var.anthropic_api_key
}
```

When ARN is provided, use `data "aws_secretsmanager_secret_version"` to read the value. When not provided, fall back to legacy `var.*` pattern. Locals resolve to whichever source is active, and the K8s secret uses only `local.*`.

### Secret Grouping

| Secret ARN variable | Keys inside |
|---|---|
| `anthropic_secret_arn` | `ANTHROPIC_API_KEY`, `ANTHROPIC_API_BASE` |
| `mcp_api_keys_secret_arn` | `MCP_ADO_API_KEY`, `MCP_ATLASSIAN_API_KEY`, `MCP_SALESFORCE_API_KEY` |
| `grafana_secret_arn` | `GRAFANA_API_KEY`, `GRAFANA_URL` |
| `ui_credentials_secret_arn` | `HOLMES_UI_USERNAME`, `HOLMES_UI_PASSWORD` |
| `datadog_secret_arn` | `DATADOG_API_KEY`, `DATADOG_APP_KEY`, `DATADOG_API_URL` |
| `pagerduty_secret_arn` | `PAGERDUTY_API_KEY`, `PAGERDUTY_USER_EMAIL`, `PAGERDUTY_WEBHOOK_SECRET` |
| `ado_webhook_secret_arn` | `ADO_WEBHOOK_USERNAME`, `ADO_WEBHOOK_PASSWORD`, `ADO_PAT`, `ADO_ORGANIZATION` |
| `salesforce_webhook_secret_arn` | `SALESFORCE_WEBHOOK_TOKEN`, `SALESFORCE_INSTANCE_URL`, `SALESFORCE_ACCESS_TOKEN` |

### Trade-offs

**Pros:** Secrets never in tfvars/TF state, can rotate without TF apply, backwards compatible.
**Cons:** Two code paths (ARN vs legacy) add complexity. First-apply requires pre-creating the secret.

---

## Issue 2: TF/Helm Apply Should Not Overwrite UI Config

### Current State

The existing DynamoDB overlay on startup already handles `enabled` and `llm_instructions` correctly — these survive Helm redeploys. The real issues:

1. **Config-level UI changes are ephemeral.** The `PUT /api/integrations/{name}/config` endpoint modifies config in memory but doesn't persist to DynamoDB.
2. **Orphaned DynamoDB state for removed toolsets.** If a toolset is removed from Helm, DynamoDB state can re-inject a broken partial config.

### Recommended: Persist Config Overrides, Ignore Orphans

**A. Persist toolset config overrides to DynamoDB.**

New DynamoDB key pattern: `TOOLSET_CONFIG | <toolset_name>` storing JSON config overrides. On startup, deep-merge DynamoDB config overrides onto Helm-provided base config.

**B. Guard against orphaned DynamoDB state.**

Only apply DynamoDB overrides for toolsets that exist in Helm config:

```python
# Skip if toolset not in Helm config
if toolset_name in (config.toolsets or {}):
    config.toolsets[toolset_name]["enabled"] = enabled
```

**C. Semantic model: Helm = catalog, DynamoDB = user preferences.**

| Field | Helm provides | DynamoDB overrides |
|---|---|---|
| Toolset existence | Yes (catalog) | No |
| `enabled` | Default | User override |
| `llm_instructions` | Default | User override |
| `config` (URLs, keys) | Base config | User override (deep merge) |

Merge order on startup:
1. Load config.yaml from Helm ConfigMap
2. Overlay `TOOLSET_STATE` from DynamoDB
3. Overlay `LLM_OVERRIDE` from DynamoDB
4. Overlay `TOOLSET_CONFIG` from DynamoDB (deep merge)

### Interaction Between Issues

Toolset config containing secrets should use `{{ env.* }}` references in Helm config, NOT store secret values in DynamoDB. DynamoDB config overrides should only store non-sensitive config (URLs, timeouts, feature flags).

---

## Files to Change

| File | Issue | Change |
|---|---|---|
| `infra/secrets.tf` | 1 | Add conditional ARN-based data sources |
| `infra/variables.tf` | 1 | Add `*_secret_arn` variables |
| `infra/helm.tf` | 1 | Unify K8s secret to use locals consistently |
| `frontend/projects.py` | 2 | Add `ToolsetConfigStore` class |
| `frontend/server_frontend.py` | 2 | Add `_restore_toolset_config_from_dynamodb`, add orphan guards |
