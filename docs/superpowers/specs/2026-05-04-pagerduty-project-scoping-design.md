# PagerDuty Project-Scoped Integration — Design

**Date**: 2026-05-04
**Status**: Proposed
**Related story**: Configure PagerDuty integration with project-scoped API key in HolmesGPT

## Problem

The existing PagerDuty toolset (`holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`) accepts a single global API key from `~/.holmes/config.yaml` or env. PagerDuty REST API v2 keys are always account-wide — there is no native "project-scoped" key. As HolmesGPT has adopted a project-first model (Datadog, DBADash, AWS already scope per-project), PagerDuty needs the same treatment:

1. A project should only see PagerDuty data relevant to it.
2. Credentials for each project should be independently rotatable and stored separately.
3. A broken or missing project instance should not break other projects.
4. Behavior should match Datadog's per-project instance model so operators have one mental model.

## Non-Goals

- Replacing the global toolset outright. It stays as a fallback for projects without instances.
- Running a fork/proxy PagerDuty API. All filtering is via existing PagerDuty query params (`team_ids[]`, `service_ids[]`).
- Write operations against PagerDuty. Toolset remains read-only.
- Splitting PagerDuty tools into sub-toolsets (`pagerduty/incidents`, `pagerduty/oncall`). The 5 tools stay in one toolset.

## Architecture

```
User → Frontend Instances page
           ↓ (POST /api/v1/instances)
       Create PagerDuty instance
         { type: "pagerduty",
           name: "pd-project-x",
           secret_arn: "arn:aws:secretsmanager:...:pd-project-x",
           config: { team_ids: [...], service_ids: [...] },
           tags: { project: "X" } }
                      ↓
              DynamoDB (INSTANCE#<id> | META)

Chat request for Project X
           ↓
   build_project_tool_executor(project)
           ↓
   resolve_instances_for_project() by tag filter
           ↓
   For each resolved PagerDuty instance:
     1. Fetch secret_arn → { api_key }
     2. Merge with instance.config → { api_key, team_ids, service_ids, default_limit }
     3. synthetic_config = {"enabled": True, "config": {...},
                            "_python_base": "pagerduty",
                            "_instance_name": instance.name }
     4. load_toolsets_from_config() → PagerDutyToolset instance
     5. check_prerequisites() → hits /services with filters, reports pass/fail
     6. Append to project_toolsets
           ↓
   ToolExecutor scoped to this project

Tool call (e.g. list_pagerduty_incidents):
           ↓
   PagerDutyConfig has team_ids/service_ids
           ↓
   _apply_scope_filters() merges user params with instance filters
   (intersection — user cannot widen beyond instance scope)
           ↓
   Single GET with team_ids[]= and service_ids[]= appended
           ↓
   Return StructuredToolResult
```

**Fallback path**: if `build_project_tool_executor` resolves zero PagerDuty instances for the project, the global `PagerDutyToolset` (loaded at startup from env `PAGERDUTY_API_KEY` / `~/.holmes/config.yaml`) is used as-is, unfiltered.

## Components

### Backend — toolset (`holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`)

- `PagerDutyConfig`: add two optional fields, both default `None`.
  - `team_ids: Optional[list[str]] = None`
  - `service_ids: Optional[list[str]] = None`
  - Keep `api_key`, `default_limit`.
- New helper `PagerDutyToolset._apply_scope_filters(query: dict, params: dict) -> tuple[dict, Optional[str]]`:
  - Parses user-supplied `service_ids`/`team_ids` params (comma-separated strings) into lists.
  - If instance has `service_ids`: intersect with user list if provided; otherwise use instance list.
  - If instance has `team_ids`: same intersect-or-set behavior.
  - Returns `(query_dict_with_filters_appended, optional_note)` where note is a string like `"Filter narrowed to project scope: service_ids=[P1]"` when user-supplied IDs were dropped.
- Tools updated:
  - `ListPagerDutyIncidents`: call `_apply_scope_filters`.
  - `ListPagerDutyServices`: call `_apply_scope_filters`.
  - `GetPagerDutyOnCall`: call `_apply_scope_filters` (maps onto `team_ids[]` and `schedule_ids[]` — note that `service_ids` does not apply to `/oncalls`, so only `team_ids` are appended here).
  - `GetPagerDutyIncident`: fetch incident, then verify `incident.service.id in instance.service_ids` when set. Out of scope → `ERROR` with message `"Incident {id} is not in this project's scope (services: {service_ids})"`.
  - `ListPagerDutyAlerts`: fetch parent incident first (1 extra API call) to verify scope, then fetch alerts. If out of scope, error before the alerts call.
- `_health_check`: hits `/services` with `team_ids[]` / `service_ids[]` when set. 200 with empty array still passes (proves auth works).

### Backend — toolset registry (`holmes/plugins/toolsets/__init__.py`)

- Add `"pagerduty": PagerDutyToolset` to `PYTHON_TOOLSET_FACTORIES`.

### Backend — project orchestration (`frontend/projects.py`)

- No code change required. The existing "Python toolset with per-project creds" path (lines ~1063–1089) already handles factory-registered toolsets. Once `pagerduty` is in `PYTHON_TOOLSET_FACTORIES`, PagerDuty instances flow through the same path Datadog/DBADash use. Instance `config` (team_ids/service_ids) and secret-derived `api_key` both end up in `synthetic_config["config"]` and are consumed by `PagerDutyConfig`.

### Backend — test-connection endpoint (`frontend/api_v1.py`)

- Extend `POST /api/v1/instances/{id}/test-connection` to handle `type == "pagerduty"`:
  1. Fetch secret_arn from Secrets Manager.
  2. Instantiate `PagerDutyToolset`, run `prerequisites_callable(merged_config)`.
  3. Return `{"success": true}` or `{"success": false, "error": "<message>"}`.
- Mirrors the existing AWS `test-connection` shape.

### Frontend — Instances UI (`frontend/src/components/Instances.tsx`)

- Add `'pagerduty'` to `TOOLSET_TYPES`.
- Add a PagerDuty-specific config block (shown when `type === 'pagerduty'`):
  - `secret_arn` field (reuses existing generic field).
  - Tag-chip editor for `service_ids` (reuse the `awsRegions` chip pattern).
  - Tag-chip editor for `team_ids` (same pattern).
- Wire the existing Test Connection button to the new backend endpoint when `type === 'pagerduty'`.

### Docs

- `docs/data-sources/builtin-toolsets/pagerduty.md`: add section covering (a) global env-var fallback usage and (b) per-project instance with team/service filters. Include a note explaining that PagerDuty API keys are account-wide and scope is enforced by the toolset via `team_ids`/`service_ids` query filters.
- `README.md`: no change (PagerDuty row already present).
- `docs/data-sources/builtin-toolsets/.nav.yml`: verify pagerduty entry present; no change expected.

## Data model

Stored in DynamoDB under `INSTANCE#<id> | META`:

```json
{
  "id": "inst_abc123",
  "type": "pagerduty",
  "name": "pd-project-x",
  "secret_arn": "arn:aws:secretsmanager:us-east-1:717423812395:secret:holmesgpt/pagerduty/project-x-AbCdEf",
  "config": {
    "team_ids": ["PTEAM1", "PTEAM2"],
    "service_ids": ["PSERVICE_ALPHA"],
    "default_limit": 25
  },
  "tags": {"project": "X"}
}
```

Secret payload shape:

```json
{ "api_key": "u+xxxxxxxxxxxxxxxxxxxx" }
```

## Error handling

| Scenario | Behavior |
|---|---|
| `secret_arn` set but Secrets Manager access denied / not found | Log warning (existing pattern), skip instance, project loses PagerDuty for this chat. Surfaced via Test Connection. |
| Secret present but missing `api_key` field | Skip with logged warning, mirroring the existing MCP pattern in `projects.py`. |
| No `secret_arn` and no `config.api_key` | Skip with warning "PagerDuty instance has no credential source". |
| Invalid / expired API key at init | `prerequisites_callable` → `/services` returns 401 → `check_prerequisites` fails, toolset not added to executor. Error surfaced in Test Connection as "PagerDuty API key is invalid or expired". |
| 401 at tool-call time (key revoked mid-session) | Catch in `get()`, return `StructuredToolResult(ERROR, error="PagerDuty API key rejected (401) — check the secret at <secret_arn>")`. The api_key itself is never included in errors or logs. |
| `GetPagerDutyIncident` for incident outside instance `service_ids` | Return `ERROR` with message `"Incident {id} is not in this project's scope (services: {service_ids})"`. |
| `ListPagerDutyAlerts` for incident outside scope | Pre-check parent incident; error before the alerts call. |
| User widens filter via tool params beyond instance scope | `_apply_scope_filters` intersects → narrowed list. Result data includes a note: `"Filter narrowed to project scope: service_ids=[P1]"`. |
| Empty result sets from filters | Not an error. `StructuredToolResult(SUCCESS, data={...incidents:[]})`, with `params` echoed to aid LLM self-correction. |
| 429 rate limit | Return `StructuredToolResult(ERROR, error="PagerDuty API rate limit exceeded (429). Retry-After: <header>")`. No auto-retry. |
| Backwards compat: old config without filter fields | New fields default to `None`. Existing `~/.holmes/config.yaml` entries continue to work unchanged. Global toolset still registered. |

## Security

- API keys are stored only in AWS Secrets Manager; never in DynamoDB, never in logs, never echoed in error messages.
- `_fetch_secret` uses the service's existing IAM role (`secretsmanager:GetSecretValue` on `holmesgpt/pagerduty/*`). No new IAM permissions needed beyond the existing pattern.
- Out-of-scope guards (`GetPagerDutyIncident`, `ListPagerDutyAlerts`) are enforced in Python code, not just prompts — they will not leak regardless of LLM behavior.
- `_apply_scope_filters` intersects rather than unions, so a compromised user prompt cannot widen the visible scope.

## Testing

### Unit tests — `tests/plugins/toolsets/test_pagerduty.py`

1. **Config model**
   - `PagerDutyConfig(api_key="x")` — old-style, succeeds, both filter fields default to `None`.
   - `PagerDutyConfig(api_key="x", team_ids=["T1"], service_ids=["P1","P2"])` — new-style, succeeds.

2. **`_apply_scope_filters`**
   - Instance has no filters, user passes nothing → no `team_ids[]`/`service_ids[]` in query.
   - Instance has `service_ids=["P1","P2"]`, user passes nothing → query has `service_ids[]=["P1","P2"]`.
   - Instance has `service_ids=["P1","P2"]`, user passes `service_ids="P1"` → intersection → `["P1"]`.
   - Instance has `service_ids=["P1"]`, user passes `service_ids="P2,P3"` → empty intersection, note added to result data.
   - Instance has both `team_ids` and `service_ids` → both appear in query.

3. **Tool behavior with filters** (mock `api.pagerduty.com` via `responses`)
   - `ListPagerDutyIncidents`: assert request URL contains `service_ids%5B%5D=P1`.
   - `ListPagerDutyServices`: assert filter applied.
   - `GetPagerDutyOnCall`: assert `team_ids[]` applied; `service_ids` absent (not valid on `/oncalls`).
   - `GetPagerDutyIncident` out-of-scope: mock returns incident with `service.id=P99`, instance has `service_ids=["P1"]` → ERROR.
   - `GetPagerDutyIncident` in-scope: mock returns `service.id=P1`, instance has `service_ids=["P1"]` → SUCCESS.
   - `ListPagerDutyAlerts` out-of-scope: parent incident check fails; mock asserts `/alerts` endpoint never hit.

4. **Health check**
   - 200 OK → `(True, "")`.
   - 401 → `(False, "PagerDuty API key is invalid or expired")`.
   - 429 → `(False, "PagerDuty API returned 429: ...")`.
   - Instance has `service_ids` → health-check URL includes `service_ids[]=` filter; 200 with empty array still passes.

5. **Error path**
   - 401 at tool-call time → `StructuredToolResult` ERROR, message includes secret_arn hint, api_key not present in error/log.
   - 429 at tool-call time → ERROR with Retry-After when header present.

### LLM eval — `tests/llm/fixtures/test_ask_holmes/<next_num>_pagerduty_project_scope/`

Cloud-service-eval pattern (no Kubernetes). Uses `responses` fixture to mock `api.pagerduty.com` — no real PagerDuty calls. Pick `<next_num>` by checking the highest existing test number under `tests/llm/fixtures/test_ask_holmes/` and incrementing by 1.

- `test_case.yaml`:
  ```yaml
  user_prompt: "List the active incidents and tell me the incident number for the one in service PSERVICE_ALPHA."
  include_tool_calls: true
  expected_output:
    - "Must call list_pagerduty_incidents tool"
    - "Must report incident number P-HOLMES-EVAL-9k4m7x2p"
    - "Must NOT report any incident from service PSERVICE_BETA"
  runbooks: {}
  ```
- `toolsets.yaml`: declares `pagerduty` toolset with `api_key: "{{ env.MOCK_PAGERDUTY_KEY }}"` and `service_ids: ["PSERVICE_ALPHA"]`.
- Mock setup inside the test file:
  - `/incidents?service_ids[]=PSERVICE_ALPHA` → one incident with ID `P-HOLMES-EVAL-9k4m7x2p`.
  - `/incidents?service_ids[]=PSERVICE_BETA` → different incident with an ID that must **not** appear in the LLM's answer.
- Verification code `P-HOLMES-EVAL-9k4m7x2p` is unique and only discoverable by querying. This rules out hallucination and proves scoping.

### Integration test — test-connection endpoint

`tests/frontend/test_instances_api.py` (extend or create):

- POST `/api/v1/instances/{id}/test-connection` for a `pagerduty` instance with mocked Secrets Manager and mocked `/services` → `{success: true}`.
- Same with 401 response → `{success: false, error: "PagerDuty API key is invalid or expired"}`.
- Missing secret_arn and no inline api_key → `{success: false, error: "PagerDuty instance has no credential source"}`.

### CI

No new GitHub secrets required — evals are fully mocked. No changes to `.github/workflows/eval-regression.yaml`.

## Acceptance criteria traceability

| Story criterion | Addressed by |
|---|---|
| PagerDuty integration supports a valid API key | Existing `PagerDutyConfig.api_key` retained; new per-project instances each carry their own key from Secrets Manager. |
| API key behavior (global vs project-scoped) is validated and confirmed | Documented in `pagerduty.md`: global env-var key acts as fallback; per-project instances scope via `team_ids`/`service_ids` filters since PagerDuty keys are inherently account-wide. |
| HolmesGPT successfully initializes the PagerDuty toolset without API key errors | `prerequisites_callable` and `_health_check` handle missing/invalid keys gracefully. Test Connection endpoint surfaces errors before chat runs. |
| Integration behavior is consistent with Datadog scoping | Registered in `PYTHON_TOOLSET_FACTORIES`, flows through the same `build_project_tool_executor` path as Datadog/DBADash, Instances UI entry identical in shape, secret_arn-first credential model identical. |

## Out of scope (future work)

- PagerDuty write operations (acknowledge/resolve/snooze incidents).
- Automatic sync of PagerDuty Teams/Services into the HolmesGPT UI (operators enter IDs manually for now).
- PagerDuty webhook → project routing beyond what already exists in `frontend/projects.py`.
