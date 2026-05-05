# Bitbucket

Query Bitbucket Cloud repositories, pull requests, commits, and file contents via a read-only Python toolset.

## Capabilities

- Repositories: list, get details.
- Pull requests: list by state, get details, fetch diff, list comments.
- Commits: list by branch/ref, get details, fetch diff.
- File contents: read any file at a specific ref (branch, commit, tag).

**Not supported**: Bitbucket Server / Data Center, Pipelines, write operations. See "Out of scope" at the bottom for reasoning.

## Configuration

Bitbucket instances are **per-project only** — there is no global fallback env var. Each project scopes to exactly one Bitbucket workspace.

### 1. Create an API token

In an Atlassian account (bot account recommended), go to **Security → API tokens**, create a new token with Bitbucket scopes, and note the value.

### 2. Store the secret

Create a Secrets Manager secret containing both the token and the workspace slug:

```json
{
  "api_token": "ATATT3xFfGF0...",
  "workspace": "pdi-logistics"
}
```

Name it descriptively, e.g. `holmesgpt-prod/bitbucket-logistics`.

### 3. Create the per-project instance

In the HolmesGPT UI:

1. Go to **Instances → New Instance**.
2. Pick type `bitbucket`, name it (e.g. `bb-logistics`).
3. Paste the Secret ARN.
4. (Optional) Add a **Repository Allowlist** to restrict this instance to specific repos within the workspace.
5. Click **Test Connection** — should return `ok: true`.
6. Tag the instance (e.g. `project=logistics`) so the matching project picks it up.

## What project scoping enforces

- Each Bitbucket instance is pinned to exactly one workspace (from the secret). Only repos in that workspace are reachable.
- If `repositories` is set, every tool call that targets a specific repo is restricted to that list — Python-enforced before any API call, so an LLM cannot widen the scope by prompt.
- File paths are validated against path-traversal patterns (`..` rejected).

## Size guards

To protect the LLM context window:

- PR and commit diffs are truncated to **200 KB** by default. Pass `max_bytes=N` to override.
- File contents are truncated to **2000 lines** by default. Pass `max_lines=N` to override.
- List endpoints cap at **100 items** per call.

## Common Queries

```
"List the last 3 pull requests in the checkout-api repo."
"Show me the diff of PR #42 in checkout-api."
"Who merged changes to src/app.py in the last week?"
"Get the contents of config/prod.yaml from main branch of inventory-db."
"What commits are on the release/2026.05 branch of checkout-api?"
```

## Troubleshooting

```bash
# Verify the secret shape
aws secretsmanager get-secret-value --secret-id holmesgpt-prod/bitbucket-logistics \
  --profile pdi-platform-all --region us-east-1 --query SecretString --output text
# Expect: {"api_token":"...","workspace":"..."}

# Test the connection end-to-end via the UI → Test Connection button.
# Expected: {"ok": true, "status": "success"}.
```

| Symptom | Likely cause |
|---|---|
| Test Connection → `rejected (401)` | API token invalid or expired. Regenerate in Atlassian account settings. |
| Test Connection → `no access to workspace` (403) | Token is valid but missing Bitbucket scopes for that workspace. Re-issue with correct scopes. |
| `Repository 'X' is not in this project's scope` | Instance has a `repositories` allowlist that excludes `X`. Add it or use a different instance. |
| `File not found` (404) | Wrong path, branch, or case. Bitbucket paths are case-sensitive. |
| Diff truncated unexpectedly | Default cap is 200 KB. Pass `max_bytes=500000` to get more. |
| 429 rate limit | Bitbucket Cloud caps at 1000 req/hour. Retry after the `Retry-After` header value. |

## Out of scope (follow-ups)

- Bitbucket Server / Data Center (different API: `/rest/api/1.0`).
- Bitbucket Pipelines (runs, logs).
- Write operations (create PR, comment, approve).
- OAuth per-user flow.
- Full pagination traversal (currently MVP: first page only, up to `limit`).
