# Atlassian + Jenkins MCP Integration — Design

**Date**: 2026-05-04
**Status**: Proposed
**Related story**: Integrate Atlassian MCP (and add Jenkins MCP) into HolmesGPT so the PDI gateway toolsets actually work in dev and prod.

## Problem

The Atlassian MCP toolset is already wired in `infra/helm.tf`, registered in `_MCP_TOOLSET_TYPES`, has LLM instructions, and appears in the Instances UI — but nobody can use it because `MCP_ATLASSIAN_API_KEY` is empty in both `holmesgpt-dev/mcp-api-keys` and `holmesgpt-prod/mcp-api-keys`. A legacy `atlassian-default` instance in prod (created 2026-03-13) has `secret_arn: null` and `mcp_url: null`, so it falls back to that empty env var and silently fails to load.

The `mcp-readonly-api-keys` secret in account 717423812395 holds valid read-only PDI gateway keys for four integrations (`ado`, `atlassian`, `salesforce`, `jenkins`). The first three are already wired in helm; `jenkins` is a fourth integration we can unlock at the same time.

Additionally, the existing `/api/instances/{id}/test-connection` endpoint only handles `aws_api` and `pagerduty`; operators have no way to sanity-check an MCP instance's credentials before asking Holmes to use it.

## Non-Goals

- Replacing the existing Python `confluence` toolset — it's used in parallel for a project that relies on basic auth + Atlassian API tokens. Leave it alone.
- Changing the secrets-from-ARN architecture defined in `docs/superpowers/specs/2026-03-23-secrets-and-ui-config-architecture.md`.
- Adding LLM evals that exercise the real PDI MCP gateway — out of scope; the gateway speaks MCP protocol and isn't trivially mockable.
- Modifying or deleting the legacy `atlassian-default` instance in prod. We rely on the env-var fallback to make it start working.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ Source of truth (read-only, already exists):                           │
│ arn:aws:secretsmanager:us-east-1:717423812395:secret:                  │
│   mcp-readonly-api-keys-L63NWI                                         │
│ { ado, atlassian, salesforce, jenkins }                                │
└────────────────────────────────────────────────────────────────────────┘
                          │
                          │ one-time migration (scripts/populate_mcp_keys.sh)
                          ▼
┌──────────────────────────────────┐   ┌──────────────────────────────────┐
│ holmesgpt-dev/mcp-api-keys       │   │ holmesgpt-prod/mcp-api-keys      │
│ {                                │   │ {                                │
│   MCP_ADO_API_KEY:       <val>,  │   │   MCP_ADO_API_KEY:       <val>,  │
│   MCP_ATLASSIAN_API_KEY: <val>,  │   │   MCP_ATLASSIAN_API_KEY: <val>,  │
│   MCP_SALESFORCE_API_KEY:<val>,  │   │   MCP_SALESFORCE_API_KEY:<val>,  │
│   MCP_JENKINS_API_KEY:   <val>   │   │   MCP_JENKINS_API_KEY:   <val>   │ ← new
│ }                                │   │ }                                │
└──────────────────────────────────┘   └──────────────────────────────────┘
                          │
                          │ loaded via helm.tf → K8s Secret → Holmes pod env
                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Holmes pod env: MCP_*_API_KEY                                          │
│                                                                        │
│ helm.tf auto-registers 4 MCP toolsets (ado, atlassian, salesforce,     │
│ jenkins) when their respective keys are non-empty.                     │
│                                                                        │
│ Per-project MCP instances:                                             │
│   - with secret_arn → fetch from SM, use instance-level key            │
│   - without secret_arn → fall back to MCP_<TYPE>_API_KEY env var       │
└────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│ RemoteMCPToolset → https://mcp-api.platform.pditechnologies.com/v1/    │
│   {ado,atlassian,salesforce,jenkins}-sse/mcp                           │
│ headers: { "x-api-key": <key> }                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**Test Connection flow (new MCP branch):**

```
POST /api/instances/{id}/test-connection  (inst.type ∈ _MCP_TOOLSET_TYPES)
   ↓
_test_mcp_instance_connection(store, inst):
   1. Resolve api_key:
      - inst.secret_arn → fetch from SM, read `api_key` field.
      - else → os.environ["MCP_<TYPE>_API_KEY"].
      - else → return {"ok": false, "error": "No credential source ..."}.
   2. Resolve URL: inst.mcp_url else _MCP_DEFAULT_URLS[inst.type].
   3. Build RemoteMCPToolset with URL + x-api-key header + LLM instructions.
   4. ts.check_prerequisites() → (bool, msg).
   5. Strip api_key substring from msg before returning (defensive).
   6. Return { ok, status, error?, tool_count? }.
```

## Scope

| Item | In scope | Out of scope |
|---|---|---|
| Populate `MCP_ADO_API_KEY`, `MCP_ATLASSIAN_API_KEY`, `MCP_SALESFORCE_API_KEY`, `MCP_JENKINS_API_KEY` in dev+prod | ✅ | — |
| Wire Jenkins MCP in `helm.tf`, `variables.tf`, `secrets.tf`, tfvars | ✅ | — |
| Add `jenkins` to `_MCP_TOOLSET_TYPES` / `_MCP_DEFAULT_URLS` / `_MCP_ICONS` / `_MCP_DESCRIPTIONS` | ✅ | — |
| Create `frontend/mcp_instructions/jenkins.jinja2` | ✅ | — |
| Add `jenkins` to `TOOLSET_TYPES` and `MCP_TYPES` in `Instances.tsx` | ✅ | — |
| Extend `/api/instances/{id}/test-connection` for MCP types | ✅ | — |
| Docs pages for Atlassian MCP and Jenkins MCP | ✅ | — |
| Legacy `atlassian-default` instance cleanup | — | ❌ (rely on env-var fallback) |
| Python `confluence` toolset | — | ❌ (keep as-is) |
| LLM eval for Atlassian / Jenkins MCP | — | ❌ (follow-up) |

## Components

### Script — `scripts/populate_mcp_keys.sh` (new)

One-time migration script (idempotent). Reads from `mcp-readonly-api-keys-L63NWI` and writes to `holmesgpt-<env>/mcp-api-keys`.

- Accepts one positional arg: `dev` or `prod`.
- Maps source keys (`ado`, `atlassian`, `salesforce`, `jenkins`) to destination keys (`MCP_ADO_API_KEY`, `MCP_ATLASSIAN_API_KEY`, `MCP_SALESFORCE_API_KEY`, `MCP_JENKINS_API_KEY`).
- Reads source using `pdi-platform-dev` profile (account 717423812395 — source secret lives here for both envs).
- Writes destination using `pdi-platform-dev` or `pdi-platform-all` profile based on arg.
- Uses `aws secretsmanager put-secret-value` (secret already exists; no create branch needed).
- Prints before/after summary (`SET`/`EMPTY` per key, no values logged).

### Backend — MCP registry (`frontend/projects.py`)

Four small additions around lines 830-850:

```python
_MCP_TOOLSET_TYPES = {"ado", "atlassian", "salesforce", "jenkins"}

_MCP_DEFAULT_URLS = {
    "ado":        "https://mcp-api.platform.pditechnologies.com/v1/ado-sse/mcp",
    "atlassian":  "https://mcp-api.platform.pditechnologies.com/v1/atlassian-sse/mcp",
    "salesforce": "https://mcp-api.platform.pditechnologies.com/v1/salesforce-sse/mcp",
    "jenkins":    "https://mcp-api.platform.pditechnologies.com/v1/jenkins-sse/mcp",
}

_MCP_ICONS = {
    "ado":        "https://cdn.simpleicons.org/azuredevops/0078D7",
    "atlassian":  "https://cdn.simpleicons.org/atlassian/0052CC",
    "salesforce": "https://cdn.simpleicons.org/salesforce/00A1E0",
    "jenkins":    "https://cdn.simpleicons.org/jenkins/D24939",
}

_MCP_DESCRIPTIONS = {
    "ado":        "Azure DevOps - work items, repositories, pipelines, and boards",
    "atlassian":  "Atlassian - Jira issues, Confluence pages, and project boards",
    "salesforce": "Salesforce - accounts, contacts, opportunities, cases, and CRM data",
    "jenkins":    "Jenkins - CI/CD jobs, builds, pipelines, and build history",
}
```

No change to `build_project_tool_executor` — existing MCP path already handles the new type.

### Backend — Helm IaC (`infra/helm.tf`)

Extend `mcp_servers`:

```hcl
mcp_servers = (local.mcp_keys["MCP_ADO_API_KEY"] != "" ||
               local.mcp_keys["MCP_ATLASSIAN_API_KEY"] != "" ||
               local.mcp_keys["MCP_SALESFORCE_API_KEY"] != "" ||
               local.mcp_keys["MCP_JENKINS_API_KEY"] != "") ? merge(
  // existing ado / atlassian / salesforce blocks unchanged,
  local.mcp_keys["MCP_JENKINS_API_KEY"] != "" ? {
    jenkins = {
      description = "Jenkins - CI/CD jobs, builds, pipelines, and build history"
      config = {
        url  = "https://mcp-api.platform.pditechnologies.com/v1/jenkins-sse/mcp"
        mode = "streamable-http"
        headers = {
          "x-api-key" = "{{ env.MCP_JENKINS_API_KEY }}"
        }
        icon_url = "https://cdn.simpleicons.org/jenkins/D24939"
      }
      llm_instructions = "Use this toolset to query Jenkins CI/CD data: jobs, builds, pipeline runs, and build console logs. Prefer specific job/build references over broad queries."
    }
  } : {}
) : {}
```

Add `MCP_JENKINS_API_KEY` to the env var / K8s secret block (mirror existing ADO entry at line ~159).

### Backend — IaC variables (`infra/variables.tf`, `infra/secrets.tf`, `infra/envs/*.tfvars.example`)

- `variables.tf`: new `variable "mcp_jenkins_api_key" { default = "" ... }`.
- `secrets.tf`: add to the `local.mcp_keys` object so it flows into helm.tf.
- Example tfvars: add `mcp_jenkins_api_key = ""` placeholder.
- Pipeline (ship skill / CI): secret migration via `scripts/populate_mcp_keys.sh` populates the Secrets Manager value; `tofu apply` reads from SM via a data source (or via the existing `sm_get` shell helper).

### Backend — Test Connection endpoint (`frontend/server_frontend.py`)

New module-level async helper `_test_mcp_instance_connection(store, inst)` placed alongside `_test_aws_instance_connection` and `_test_pagerduty_instance_connection` (module-level, above `mount_frontend`):

```python
async def _test_mcp_instance_connection(store, inst):
    """Test an MCP instance by building a RemoteMCPToolset and running
    check_prerequisites. Returns dict payload for JSONResponse.
    """
    from projects import (  # noqa: PLC0415
        _build_mcp_toolset,
        _fetch_secret,
        _instance_to_toolset_instance,
    )

    # Resolve api_key.
    if inst.secret_arn:
        try:
            creds = _fetch_secret(inst.secret_arn)
        except Exception as e:
            return {"ok": False, "status": "error", "error": f"Failed to fetch secret: {e}"}
        api_key = creds.get("api_key") or creds.get("x-api-key") or ""
        if not api_key:
            return {"ok": False, "status": "error", "error": "Secret has no 'api_key' field"}
    else:
        env_var = f"MCP_{inst.type.upper()}_API_KEY"
        api_key = os.environ.get(env_var, "")
        if not api_key:
            return {
                "ok": False,
                "status": "error",
                "error": (
                    f"No credential source: set secret_arn on the instance or "
                    f"populate {env_var} in the pod environment"
                ),
            }

    # Convert Instance → ToolsetInstance (what _build_mcp_toolset expects).
    tsi = _instance_to_toolset_instance(inst)
    try:
        ts = _build_mcp_toolset(tsi, api_key)
    except ValueError as e:
        return {"ok": False, "status": "error", "error": str(e)}

    ok, msg = ts.check_prerequisites()

    # Defensive: strip api_key from any error message before returning.
    if msg and api_key and api_key in msg:
        msg = msg.replace(api_key, "<redacted>")

    if ok:
        return {
            "ok": True,
            "status": "success",
            "tool_count": len(getattr(ts, "tools", [])),
        }
    return {"ok": False, "status": "error", "error": msg}
```

Dispatcher update in `test_instance_connection` route:

```python
if inst.type == "aws_api":
    body = await _test_aws_instance_connection(store, inst)
    return JSONResponse(body)
if inst.type == "pagerduty":
    body = await _test_pagerduty_instance_connection(store, inst)
    return JSONResponse(body)
if inst.type in _MCP_TOOLSET_TYPES:    # ← new
    body = await _test_mcp_instance_connection(store, inst)
    return JSONResponse(body)

raise HTTPException(status_code=400, detail=f"test-connection not supported for type '{inst.type}'")
```

### LLM instructions — `frontend/mcp_instructions/jenkins.jinja2` (new)

```
Use this toolset to investigate CI/CD issues via Jenkins.

**Jobs and builds:**
- Fetch a specific build by job name + build number when a user references one.
- List recent builds for a job to find the last failure or regression point.
- Check build duration trends to spot performance regressions.

**Console logs:**
- When a build fails, fetch the console log and search for common failure signatures:
  - Compilation errors: `error:`, `cannot find symbol`, `undefined reference`
  - Test failures: `FAIL`, `FAILED`, `AssertionError`, `expected: ... actual:`
  - Resource issues: `OutOfMemoryError`, `No space left on device`, `timeout`
- Quote the specific log lines that identify the root cause; don't dump the whole log.

**Pipelines:**
- For pipeline jobs, identify which stage failed (setup, build, test, deploy).
- Cross-reference a failed pipeline with any upstream dependency changes.

**Best practices:**
- Scope queries to a specific job name when possible; avoid listing all jobs on a busy instance.
- When a user asks "why did X break?", fetch the last successful build AND the first failing build to narrow the change window.
```

### Frontend (`frontend/src/components/Instances.tsx`)

Two one-line additions:

```typescript
const TOOLSET_TYPES = [
  // ...existing entries...
  'salesforce',
  'kubernetes',
  'dbdash',
  'pagerduty',
  'jenkins',   // new
]

const MCP_TYPES = new Set(['ado', 'atlassian', 'salesforce', 'jenkins'])  // added 'jenkins'
```

No other UI changes — MCP instance rendering already handles `secret_arn` + `mcp_url` generically.

### Docs

New page `docs/data-sources/builtin-toolsets/atlassian-mcp.md`:
- Overview (Jira + Confluence via PDI MCP gateway).
- Global fallback config (via env var / tofu).
- Per-project instance config (secret_arn pattern).
- JQL / CQL examples.
- Troubleshooting (401 → key invalid; empty tool list → gateway down; etc.).

New page `docs/data-sources/builtin-toolsets/jenkins-mcp.md`:
- Overview.
- Same two config modes as above.
- Example queries (build status, console log fetch).
- Troubleshooting.

Update `docs/data-sources/builtin-toolsets/.nav.yml`:
- Insert `- Atlassian (MCP): atlassian-mcp.md` alphabetically (between `Azure ...` and `Bash`).
- Insert `- Jenkins (MCP): jenkins-mcp.md` alphabetically (between `Inspektor Gadget` and `Kafka`).

## Data model

No schema changes — reuses existing `Instance` fields:

```json
{
  "id": "...",
  "type": "jenkins",                 // new valid value
  "name": "jenkins-logistics",
  "secret_arn": "arn:aws:...",       // optional; falls back to env var if null
  "mcp_url": null,                   // optional override; default is pdi gateway
  "config": null,
  "tags": {"project": "logistics"}
}
```

Secret payload (when `secret_arn` is set):

```json
{ "api_key": "pdi-mcp-readonly-xxxxxxxxxxx" }
```

## Error handling

| Scenario | Behavior |
|---|---|
| Source secret missing on migration | Script exits non-zero naming the ARN; no writes. |
| Source has unknown field | Log warning; proceed with known keys. |
| Destination secret doesn't exist | `create-secret`; else `put-secret-value`. Idempotent. |
| `MCP_JENKINS_API_KEY` empty after migration | `helm.tf` guard keeps jenkins out of `mcp_servers`; rest of system unaffected. |
| Jenkins MCP gateway 404 / down at runtime | `RemoteMCPToolset.check_prerequisites` fails → toolset excluded; other toolsets unaffected. |
| `_test_mcp_instance_connection` — secret fetch fails | `{ok: false, error: "Failed to fetch secret: <msg>"}`. |
| Secret present, no `api_key` field | `{ok: false, error: "Secret has no 'api_key' field"}`. |
| No `secret_arn` and env var empty | `{ok: false, error: "No credential source: set secret_arn ... or populate MCP_<TYPE>_API_KEY"}`. |
| `check_prerequisites` error contains api_key | Replaced with `<redacted>` before returning to UI. |
| Unknown MCP type (future) with no URL entry | `{ok: false, error: "No URL configured for MCP toolset type '<type>'"}`. |
| Legacy `atlassian-default` global instance | Works automatically once `MCP_ATLASSIAN_API_KEY` is populated (env-var fallback). |

## Security

- API keys never logged, never embedded in error messages, defensively redacted from MCP error strings.
- Source secret `mcp-readonly-api-keys-L63NWI` uses a restricted IAM policy (verified: account 717423812395 only, read-only).
- Migration script uses SSO profiles (`pdi-platform-dev`, `pdi-platform-all`) — no static credentials.
- Secrets Manager rotation is manual (same as all existing holmesgpt secrets); not part of this work.

## Deployment order (hard gate)

**Dev first, verified end-to-end before prod touches the same code path.**

1. Run `scripts/populate_mcp_keys.sh dev` → populates `holmesgpt-dev/mcp-api-keys`.
2. Code deploy dev: build + push image, `tofu apply -var-file=envs/dev.tfvars`, restart deployment.
3. Smoke-test from Chrome against `holmesgpt.dev.platform.pditechnologies.com`:
   - Confirm `atlassian` and `jenkins` appear in Instances dropdown.
   - Click Test Connection on the `atlassian-default` instance in prod-equivalent project (if one exists in dev; else create a test instance) → expect ✅ with tool_count > 0.
   - Ask Holmes to query one Jira issue and one Jenkins job → confirm tool calls succeed.
4. Only after dev verification:
   - `scripts/populate_mcp_keys.sh prod` → populates `holmesgpt-prod/mcp-api-keys`.
   - Code deploy prod: build + push image to prod ECR, `tofu apply -var-file=envs/prod.tfvars`, restart.
5. Smoke-test from Chrome against `holmesgpt.shared.platform.pditechnologies.com` (same three checks as step 3).

## Testing

### Unit tests — `tests/frontend/test_instances_api.py`

Add `TestMcpConnectionHelper` class, 4 tests:

1. `test_atlassian_connection_success_via_secret_arn` — mock `_fetch_secret` → `{"api_key": "real"}`; patch `RemoteMCPToolset.check_prerequisites` → `(True, "")`; assert `{"ok": True, "status": "success", "tool_count": int}`.
2. `test_jenkins_connection_success_via_env_fallback` — `inst.secret_arn = None`; monkeypatch `MCP_JENKINS_API_KEY="env-key"`; patch `check_prerequisites` → `(True, "")`; assert success.
3. `test_atlassian_connection_401_strips_api_key_from_error` — `_fetch_secret` → `{"api_key": "sk_live_abc123"}`; `check_prerequisites` → `(False, "HTTP 401: token 'sk_live_abc123' rejected")`; assert `"sk_live_abc123"` not in `body["error"]`.
4. `test_mcp_no_credential_source` — no secret_arn, no env var → assert `"No credential source" in body["error"]`.

### Manual verification post-deploy (dev, then prod)

- Instances page lists `atlassian`, `jenkins` in dropdown.
- Legacy `atlassian-default` instance in prod: Test Connection returns `ok: true` after prod deploy.
- Create one `jenkins` instance tagged for a real project; verify chat can list jobs.
- `poetry run pytest tests/plugins/toolsets/test_pagerduty.py tests/frontend/ -v` → 28 + 4 existing + 4 new = 36 tests passing.

### CI

- No new GitHub Actions secrets required (Secrets Manager is the source of truth).
- `.github/workflows/pdi-iac.yaml` already reads `holmesgpt-<env>/mcp-api-keys` at apply time; `MCP_JENKINS_API_KEY` added to the `sm_get` calls in the workflow (or in the `ship` skill's tofu invocation).

## Acceptance criteria

| Criterion | Addressed by |
|---|---|
| Atlassian MCP actually loads in prod | `scripts/populate_mcp_keys.sh prod` populates the key; env-var fallback loads the legacy instance. |
| Jenkins MCP is usable from Holmes | New helm entry + registry constants + docs. |
| Operators can sanity-check an MCP instance before deploy | New `_test_mcp_instance_connection` handler, UI button already wired. |
| Discoverable docs | Two new pages in `docs/data-sources/builtin-toolsets/` + nav entries. |
| Dev-then-prod gate | Explicit ordering in "Deployment order" section. |

## Out of scope (follow-ups)

- LLM eval that exercises Atlassian MCP (needs a mock MCP server).
- LLM eval that exercises Jenkins MCP.
- Cleaning up the legacy `atlassian-default` instance in prod.
- Secrets auto-rotation from `mcp-readonly-api-keys` to the env-specific secrets.
- Migrating the Python `confluence` toolset to MCP.
