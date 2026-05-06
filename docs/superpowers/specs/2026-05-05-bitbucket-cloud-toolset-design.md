# Bitbucket Cloud Python Toolset — Design

**Date**: 2026-05-05
**Status**: Proposed
**Related story**: Add Bitbucket Cloud integration to HolmesGPT so projects can correlate incidents with code, PRs, and commits.

## Problem

HolmesGPT can already reach Jira + Confluence (via the Atlassian MCP) and ADO (via the ADO MCP), but has no way to query Bitbucket repositories. PDI teams using Bitbucket Cloud cannot ask Holmes questions like "who changed `checkout.py` last week" or "what PRs merged into `main` yesterday" without switching tools.

The PDI MCP gateway does not currently expose a `bitbucket` endpoint (source secret `mcp-readonly-api-keys-L63NWI` has `ado`, `atlassian`, `salesforce`, `jenkins` — no `bitbucket`). Rather than wait for gateway support, we ship a **thin Python toolset** that talks directly to the Bitbucket Cloud REST API v2.0, following the established `dbdash` / `pagerduty` patterns already in the codebase.

## Non-Goals

- **Bitbucket Server / Data Center**: different API shape (`/rest/api/1.0`), separate work.
- **Bitbucket Pipelines**: out of MVP scope; teams using Pipelines can request it as follow-up.
- **Write operations** (create PR, comment, approve): read-only by design.
- **OAuth per-user flow**: bot-account Bearer tokens are sufficient for ops use.
- **Full pagination traversal**: MVP returns first page only (up to `limit`). Sufficient for triage.
- **LLM eval**: deferred — would need a real Bitbucket sandbox or mock server.

## Architecture

```
User creates Bitbucket instance in UI
  type:       bitbucket
  name:       bb-logistics
  secret_arn: arn:aws:secretsmanager:us-east-1:<acct>:secret:holmesgpt-<env>/bitbucket-logistics
    secret payload: { "api_token": "ATATT...", "workspace": "pdi-logistics" }
  config:     { "repositories": ["checkout-api", "inventory-db"] }   # optional allowlist
  tags:       { "project": "logistics" }
        ↓
     DynamoDB (INSTANCE#<id> | META)

Chat request for Project "logistics"
        ↓
build_project_tool_executor(project) resolves instances by tag
        ↓
For each bitbucket instance:
  1. Fetch secret_arn → { api_token, workspace }
  2. Merge with instance.config → { api_token, workspace, repositories, default_limit, api_url }
  3. Synthetic config: {"enabled": True, "config": {...},
                        "_python_base": "bitbucket",
                        "_instance_name": "bb-logistics"}
  4. load_toolsets_from_config() → BitbucketToolset
  5. check_prerequisites() → GET /workspaces/{workspace} with Bearer token
  6. Append to project_toolsets
        ↓
Tool call (e.g. list_pull_requests):
  BitbucketToolset.get(path, params, headers={"Authorization": f"Bearer {token}"})
  → https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests
  → _check_repo_in_scope({repo}) before the call (if allowlist set)
  → _truncate(response, 200 KB) for diff endpoints
  → Return StructuredToolResult
```

**Fallback path**: if `build_project_tool_executor` resolves zero Bitbucket instances for the project, Bitbucket is simply absent from that project's toolbox. There is no global fallback env var — Bitbucket is per-project-only (its access scope is tied to a specific workspace, so a global fallback would leak across projects).

## Scope

| Item | In scope | Out of scope |
|---|---|---|
| Bitbucket Cloud REST API v2.0 | ✅ | — |
| Bitbucket Server / Data Center | — | ❌ (separate spec if needed) |
| Bearer token auth (API token) | ✅ | — |
| App password / OAuth | — | ❌ |
| Repositories (list/get) | ✅ | — |
| Pull requests (list/get/diff/comments) | ✅ | — |
| Commits (list/get/diff) | ✅ | — |
| File contents at ref | ✅ | — |
| Pipelines (runs/logs) | — | ❌ (follow-up) |
| Write operations | — | ❌ |
| Per-project instance + workspace scoping | ✅ | — |
| Per-project optional `repositories` allowlist | ✅ | — |
| Test Connection UI support | ✅ | — |
| Docs page | ✅ | — |
| Unit tests (~35) + Integration tests (3) | ✅ | — |
| LLM eval | — | ❌ (follow-up) |

## Components

### Toolset (`holmes/plugins/toolsets/bitbucket/`)

Two files: `__init__.py` (empty) and `toolset_bitbucket.py`.

**`BitbucketConfig(ToolsetConfig)`**:

```python
class BitbucketConfig(ToolsetConfig):
    api_token: str = Field(
        title="API Token",
        description="Bitbucket API token (Atlassian token with Bitbucket scopes).",
    )
    workspace: str = Field(
        title="Workspace",
        description="Bitbucket workspace slug (e.g. 'pdi-logistics').",
    )
    api_url: str = Field(
        default="https://api.bitbucket.org/2.0",
        description="Bitbucket API base URL (override for on-prem/testing).",
    )
    default_limit: int = Field(default=25, description="Default page size for list endpoints.")
    repositories: Optional[List[str]] = Field(
        default=None,
        description="Optional allowlist of repo slugs. When set, restricts every tool call to these repos.",
    )
```

**`BitbucketToolset(Toolset)`** helpers:
- `_headers()` → `{"Authorization": f"Bearer {api_token}", "Accept": "application/json"}`.
- `_health_check()` → `GET /workspaces/{workspace}`, maps 200/401/403 to clear messages.
- `get(path, params)` → wraps `requests.get` with auth. Maps 401 → `BitbucketAuthError`, 403 → `BitbucketForbiddenError`, 429 → `BitbucketRateLimitError(retry_after)`, 404 bubbles for callers to handle as "not found".
- `_check_repo_in_scope(repo_slug, params) -> Optional[StructuredToolResult]`: None when allowed; ERROR when blocked. Normalizes case.
- `_truncate(text, max_bytes, *, line_mode=False)`: line-boundary trim with `[... truncated N bytes/lines ...]` marker.
- `_validate_repo_slug(slug) -> bool`: regex `^[a-z0-9._-]+$`.
- `_validate_ref(ref) -> bool`: regex `^[A-Za-z0-9._/-]{1,255}$` AND `".." not in ref`.

**10 tool classes** — each follows the pattern: validate input, scope-check, build query, call `self.toolset.get`, return `StructuredToolResult`. Names and endpoints:

| Tool | Endpoint | Truncation |
|---|---|---|
| `ListBitbucketRepositories` | `GET /repositories/{workspace}` | limit (default 25, max 100) |
| `GetBitbucketRepository` | `GET /repositories/{workspace}/{repo}` | — |
| `ListBitbucketPullRequests` | `GET /repositories/{workspace}/{repo}/pullrequests?state=...` | limit |
| `GetBitbucketPullRequest` | `GET /repositories/{workspace}/{repo}/pullrequests/{id}` | — |
| `GetBitbucketPullRequestDiff` | `GET /repositories/{workspace}/{repo}/pullrequests/{id}/diff` | 200 KB |
| `ListBitbucketPullRequestComments` | `GET /repositories/{workspace}/{repo}/pullrequests/{id}/comments` | limit |
| `GetBitbucketCommit` | `GET /repositories/{workspace}/{repo}/commit/{sha}` | — |
| `ListBitbucketCommits` | `GET /repositories/{workspace}/{repo}/commits/{branch}` | limit |
| `GetBitbucketCommitDiff` | `GET /repositories/{workspace}/{repo}/diff/{sha}` | 200 KB |
| `GetBitbucketFileContents` | `GET /repositories/{workspace}/{repo}/src/{ref}/{path}` | 2000 lines |

### Toolset registry (`holmes/plugins/toolsets/__init__.py`)

- Import `BitbucketToolset` near the other toolset imports.
- Append `BitbucketToolset()` to the global toolset list.
- Add `"bitbucket": BitbucketToolset` to `PYTHON_TOOLSET_FACTORIES` so per-project instances flow through the same path as Datadog / DBADash / PagerDuty.

No change to `frontend/projects.py` — the existing Python toolset path (lines ~1063-1089) already handles factory-registered toolsets once `bitbucket` is in `PYTHON_TOOLSET_FACTORIES`.

### Frontend Instances UI (`frontend/src/components/Instances.tsx`)

- Add `'bitbucket'` to `TOOLSET_TYPES`.
- Do **not** add to `MCP_TYPES` (it's a Python toolset).
- When `type === 'bitbucket'`:
  - Show read-only help text: "Store a Secrets Manager secret with `{\"api_token\": \"...\", \"workspace\": \"...\"}` and paste its ARN above. Optionally restrict this instance to a subset of repos."
  - Render a `<ChipListEditor>` for `repositories` (reuses the existing component). Values wire into `config.repositories`.
- Save payload: when `isBitbucket`, emit `config: { repositories: [...] }` if non-empty, else null. Preserve existing config for other types.

### Test Connection endpoint (`frontend/server_frontend.py`)

Add a new module-level async helper `_test_bitbucket_instance_connection(store, inst)` alongside the existing AWS / PagerDuty / MCP helpers:

```python
async def _test_bitbucket_instance_connection(store, inst):
    """Test a Bitbucket instance by fetching the workspace."""
    from projects import _fetch_secret  # noqa: PLC0415
    from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import (  # noqa: PLC0415
        BitbucketToolset,
    )

    if not inst.secret_arn:
        return {"ok": False, "status": "error", "error": "Bitbucket instance has no credential source (secret_arn required)"}
    try:
        creds = _fetch_secret(inst.secret_arn)
    except Exception as e:
        return {"ok": False, "status": "error", "error": f"Failed to fetch secret: {e}"}
    if "api_token" not in creds or "workspace" not in creds:
        return {"ok": False, "status": "error", "error": "Secret must contain `api_token` and `workspace` fields"}

    cfg = {**creds, **(inst.config or {})}
    ts = BitbucketToolset()
    ok, msg = ts.prerequisites_callable(cfg)
    return {"ok": ok, "status": "success" if ok else "error", **({} if ok else {"error": msg})}
```

Extend the dispatcher in `test_instance_connection` route with an `elif inst.type == "bitbucket"` branch.

### Documentation

New `docs/data-sources/builtin-toolsets/bitbucket.md`:

- Overview (Cloud-only, read-only, per-project only — no global).
- Secret format: `{"api_token": "ATATT...", "workspace": "pdi-logistics"}`.
- Creating an API token: Atlassian account → Security → API tokens, scope to Bitbucket.
- UI recipe: Instances → New Instance → type `bitbucket`, set secret ARN, optional repo allowlist, Test Connection.
- Tool list.
- Troubleshooting (401, 403, 404, 429, size truncation markers).

Add to `docs/data-sources/builtin-toolsets/.nav.yml` alphabetically: `- Bitbucket: bitbucket.md` between `Bash` and `Cilium`.

## Data model

Stored in DynamoDB under `INSTANCE#<id> | META`:

```json
{
  "id": "inst_xyz789",
  "type": "bitbucket",
  "name": "bb-logistics",
  "secret_arn": "arn:aws:secretsmanager:us-east-1:827852520868:secret:holmesgpt-prod/bitbucket-logistics-AbCdEf",
  "config": {
    "repositories": ["checkout-api", "inventory-db"]
  },
  "tags": {"project": "logistics"},
  "created_at": "2026-05-05T..."
}
```

Secret payload shape:

```json
{
  "api_token": "ATATT3xFfGF0...",
  "workspace": "pdi-logistics"
}
```

## Error handling

| Scenario | Behavior |
|---|---|
| `secret_arn` missing | Skip instance, log warning, project loses Bitbucket for this chat. Test Connection surfaces `"no credential source"`. |
| Secret missing `api_token` or `workspace` | Skip, log warning. Test Connection returns clear message. |
| 401 at init or tool call | `BitbucketAuthError`: "Bitbucket API token rejected (401). Check the secret configured for this instance." Token never in error text. |
| 403 at init or tool call | `BitbucketForbiddenError`: "Token has no access to workspace '{workspace}' (or to this resource). Verify the token's Repository scopes." |
| 404 at tool call | `StructuredToolResult(ERROR, error="{Resource} {id} not found")`. Distinct from 403 so LLM doesn't hallucinate existence. |
| 429 | `BitbucketRateLimitError`: "Bitbucket API rate limit exceeded (429). Retry-After: {header}". No client-side retry. |
| Repo not in allowlist | ERROR before any API call: `"Repository '{slug}' is not in this project's scope (allowed: {list})"`. |
| Invalid `repo_slug` (path traversal attempt) | ERROR before API call: `"Invalid repo_slug: must match [a-z0-9._-]+"`. |
| Invalid `ref` (contains `..`) | ERROR before API call. |
| Diff > 200 KB | Truncate on line boundary, trailing marker. LLM can retry with larger `max_bytes` param. |
| File > 2000 lines | Truncate on line boundary, trailing marker. |
| List endpoint > 100 items requested | Cap at 100, log warning, response data includes `_truncated: true` hint. |

## Security

- Tokens stored only in AWS Secrets Manager; never in DynamoDB, never in logs.
- Error messages defensively strip the token (verbatim, stripped, URL-encoded variants) before returning to UI/LLM — same redaction helper pattern as the MCP Test Connection handler.
- Path-traversal prevention: `_validate_repo_slug` and `_validate_ref` regex checks before URL interpolation.
- `_check_repo_in_scope` is enforced in Python, not via prompt — cannot be defeated by LLM behavior.
- IAM: `_fetch_secret` uses the existing pod IAM role with `secretsmanager:GetSecretValue` on `holmesgpt-<env>/bitbucket-*`. No new IAM permissions needed beyond pattern established by PagerDuty.

## Testing

### Unit tests — `tests/plugins/toolsets/test_bitbucket.py` (~35 tests)

1. **Config model** (4): min config, with allowlist, missing required `workspace`, custom `api_url`.
2. **`_check_repo_in_scope`** (4): no allowlist, in-scope, out-of-scope, case normalization.
3. **`_truncate`** (3): under limit, byte-based trim, line-based trim.
4. **`_health_check`** (4): 200, 401, 403, network exception.
5. **Tool behavior** (10, one per tool): mock endpoint, assert URL + params, assert SUCCESS shape.
6. **Scope guards on repo-specific tools** (7): allowlist `["a"]`, request `"b"` → ERROR, API not called.
7. **Diff / file truncation** (2): 500 KB diff truncated, 5000-line file truncated.
8. **Runtime error mapping** (4): 401, 403, 404, 429 (with `Retry-After`).
9. **Input validation** (3): invalid `repo_slug`, invalid `ref` (contains `..`), empty `pull_request_id`.
10. **Factory registration** (1): `"bitbucket" in PYTHON_TOOLSET_FACTORIES`.

### Integration tests — `tests/frontend/test_instances_api.py` (+3 tests)

New `TestBitbucketConnectionHelper` class:
- `test_bitbucket_connection_success_via_secret_arn`.
- `test_bitbucket_connection_403_returns_clear_error`.
- `test_bitbucket_no_credential_source`.

### Full regression after implementation

```bash
poetry run pytest tests/plugins/toolsets/test_pagerduty.py tests/plugins/toolsets/test_bitbucket.py tests/frontend/ -v --no-cov
```

Expected: 28 (pagerduty) + ~35 (bitbucket) + 13 (frontend, 10 existing + 3 new) = **76 tests passing**.

### Manual verification post-deploy

1. Create a Bitbucket API token in an Atlassian account and store as `{"api_token": "...", "workspace": "pdi-logistics"}` in Secrets Manager.
2. Create a per-project Bitbucket instance in the Instances UI tagged to a known project.
3. Click Test Connection → expect `{ok: true}`.
4. In Chat (project scope): `"List the last 3 pull requests in the checkout-api repo."` Expect Holmes to call `list_bitbucket_pull_requests` and return real PRs.
5. `"Show me the diff of PR #42 in checkout-api."` Expect `get_bitbucket_pull_request_diff` with truncation if large.

### CI

No new GitHub Actions secrets required — credentials read from Secrets Manager at runtime.

## Acceptance criteria

| Criterion | Addressed by |
|---|---|
| Holmes can query Bitbucket Cloud | `BitbucketToolset` + 10 tools |
| Per-project scoping (workspace + optional repo allowlist) | `BitbucketConfig.workspace` + `.repositories` + `_check_repo_in_scope` |
| Test Connection works from UI | New `_test_bitbucket_instance_connection` helper + dispatcher branch |
| API tokens never leaked in errors | `_strip_token_variants` in `get()` error mapping, matches MCP helper pattern |
| Matches existing patterns | Mirrors `pagerduty` + `dbdash` toolset shape |
| Docs + nav | New `bitbucket.md` + `.nav.yml` entry |
| Tests | ~35 unit + 3 integration |

## Out of scope (follow-ups)

- Bitbucket Server / Data Center support.
- Bitbucket Pipelines (runs, logs).
- Write operations (create PR, comment, approve).
- OAuth per-user flow.
- Full pagination traversal (currently MVP: first page only).
- LLM eval with mock Bitbucket API.
