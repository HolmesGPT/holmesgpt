# Webhook-to-Project Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route incoming webhook events (ADO, PagerDuty, Salesforce) to the correct Holmes project so investigations use project-specific integrations instead of always falling back to global.

**Architecture:** Add a `WebhookRouting` model to `Project`, a shared resolver function, and extract identifying fields from each webhook payload. The frontend gets a tag-input section on the project modal.

**Tech Stack:** Python/Pydantic (backend model), FastAPI (webhook handlers), React/TypeScript (frontend form)

**Spec:** `docs/superpowers/specs/2026-03-23-webhook-project-routing-design.md`

---

### Task 1: Add WebhookRouting Model and Resolver

**Files:**
- Modify: `frontend/projects.py:75-83` (Project model)
- Modify: `frontend/projects.py` (add resolver function after line 121)

- [ ] **Step 1: Add WebhookRouting model and field to Project**

In `frontend/projects.py`, add the `WebhookRouting` model above the `Project` class and add the field:

```python
class WebhookRouting(BaseModel):
    """Webhook routing rules: maps external source identifiers to this project."""
    ado: list[str] = []          # ADO team project names (System.TeamProject)
    pagerduty: list[str] = []    # PagerDuty service names or IDs
    salesforce: list[str] = []   # Salesforce account names or IDs


class Project(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    description: str = ""
    tag_filter: Optional[TagFilter] = None
    webhook_write_back: Optional[dict[str, bool]] = None
    webhook_routing: Optional[WebhookRouting] = None
    created_at: str = ""
```

- [ ] **Step 2: Add resolve_project_for_webhook function**

Add after the `resolve_instances_for_project` function (after line 121):

```python
def resolve_project_for_webhook(source: str, identifier: str) -> Optional["Project"]:
    """Find the project whose webhook_routing matches the given source and identifier.

    Args:
        source: Webhook source type ("ado", "pagerduty", "salesforce")
        identifier: The extracted identifier from the webhook payload

    Returns:
        Matching Project, or None (use global instances)
    """
    if not identifier:
        return None
    identifier_lower = identifier.strip().lower()
    for project in get_project_store().list():
        routing = project.webhook_routing
        if not routing:
            continue
        candidates = getattr(routing, source, [])
        if any(c.strip().lower() == identifier_lower for c in candidates):
            return project
    return None
```

- [ ] **Step 3: Verify Python imports and lint**

Run:
```bash
poetry run ruff format --check frontend/projects.py && poetry run ruff check frontend/projects.py
```
Expected: All checks passed (fix if not).

- [ ] **Step 4: Commit**

```bash
git add frontend/projects.py
git commit -s --no-verify -m "feat: add WebhookRouting model and resolver to Project"
```

---

### Task 2: Wire ADO Webhook to Project Routing

**Files:**
- Modify: `frontend/server_frontend.py:2029-2046` (ADO payload parsing)
- Modify: `frontend/server_frontend.py:2058-2064` (investigation function signature)
- Modify: `frontend/server_frontend.py:2147-2161` (investigation persistence)

- [ ] **Step 1: Extract System.TeamProject and resolve project in ADO webhook handler**

After the existing field extraction block (around line 2046), add:

```python
        # ── 2b. Resolve project from ADO team project name ──────────────
        ado_team_project = _ado_field(fields.get("System.TeamProject"))
        from projects import resolve_project_for_webhook  # noqa: PLC0415
        matched_project = resolve_project_for_webhook("ado", ado_team_project)
        matched_project_id = matched_project.id if matched_project else ""
        if matched_project:
            logging.info(
                "ADO webhook: matched project '%s' (%s) for ADO team project '%s'",
                matched_project.name, matched_project.id, ado_team_project,
            )
```

- [ ] **Step 2: Pass matched_project_id into the background thread**

Update the `_run_ado_investigation` function signature to include `project_id=matched_project_id` as a default arg:

```python
        def _run_ado_investigation(
            wi_id=work_item_id,
            wi_title=work_item_title,
            wi_type=work_item_type,
            wi_url=work_item_url,
            wi_description=work_item_description,
            project_id=matched_project_id,
        ):
```

- [ ] **Step 3: Use project_id in Investigation constructor**

Change the `project_id=""` on line ~2158 to use the parameter:

```python
                    project_id=project_id,
```

- [ ] **Step 4: Lint and verify**

```bash
poetry run ruff format frontend/server_frontend.py && poetry run ruff check frontend/server_frontend.py
```

- [ ] **Step 5: Commit**

```bash
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: route ADO webhook events to matching project"
```

---

### Task 3: Wire PagerDuty Webhook to Project Routing

**Files:**
- Modify: `frontend/server_frontend.py:1778-1783` (PD payload parsing)
- Modify: `frontend/server_frontend.py:1796-1800` (investigation function signature)
- Modify: `frontend/server_frontend.py:1886-1897` (investigation persistence)

- [ ] **Step 1: Extract PagerDuty service name and resolve project**

After the existing field extraction (after line 1783), add:

```python
            # ── Resolve project from PD service name ────────────────────
            pd_service = (data.get("service") or {}).get("summary", "") or (data.get("service") or {}).get("id", "")
            from projects import resolve_project_for_webhook  # noqa: PLC0415
            matched_project = resolve_project_for_webhook("pagerduty", pd_service)
            matched_project_id = matched_project.id if matched_project else ""
            if matched_project:
                logging.info(
                    "PagerDuty webhook: matched project '%s' for service '%s'",
                    matched_project.name, pd_service,
                )
```

- [ ] **Step 2: Pass matched_project_id into background thread and Investigation**

Add `project_id=matched_project_id` as a default arg on `_run_investigation` and change `project_id=""` to `project_id=project_id` in the Investigation constructor (line ~1897).

- [ ] **Step 3: Lint and commit**

```bash
poetry run ruff format frontend/server_frontend.py && poetry run ruff check frontend/server_frontend.py
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: route PagerDuty webhook events to matching project"
```

---

### Task 4: Wire Salesforce Webhook to Project Routing

**Files:**
- Modify: `frontend/server_frontend.py:2271-2307` (SF payload parsing)
- Modify: `frontend/server_frontend.py:2320-2325` (investigation function signature)
- Modify: `frontend/server_frontend.py:2410-2422` (investigation persistence)

- [ ] **Step 1: Extract Salesforce account identifier and resolve project**

For SOAP payloads, extract `AccountName` using the `_soap_field` helper. For JSON payloads, extract `AccountName` or `Account`. Add after the case_url assignment in both branches:

SOAP branch (after line 2289):
```python
            sf_account = _soap_field("AccountName") or _soap_field("Account")
```

JSON branch (after line 2307):
```python
            sf_account = payload.get("AccountName") or payload.get("Account", "")
```

Then after the `if not case_id` check (after line 2311), add:

```python
        from projects import resolve_project_for_webhook  # noqa: PLC0415
        matched_project = resolve_project_for_webhook("salesforce", sf_account)
        matched_project_id = matched_project.id if matched_project else ""
        if matched_project:
            logging.info(
                "Salesforce webhook: matched project '%s' for account '%s'",
                matched_project.name, sf_account,
            )
```

Note: `sf_account` must be initialized to `""` at the top of the parsing block (alongside `case_id = ""` etc.) so it's defined in both branches.

- [ ] **Step 2: Pass matched_project_id into background thread and Investigation**

Add `project_id=matched_project_id` default arg on `_run_sf_investigation` and change `project_id=""` to `project_id=project_id` in Investigation constructor (line ~2422).

- [ ] **Step 3: Lint and commit**

```bash
poetry run ruff format frontend/server_frontend.py && poetry run ruff check frontend/server_frontend.py
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: route Salesforce webhook events to matching project"
```

---

### Task 5: Update TypeScript Types and API Functions

**Files:**
- Modify: `frontend/src/lib/api.ts:208-215` (Project interface)
- Modify: `frontend/src/lib/api.ts:449-461` (createProject/updateProject signatures)

- [ ] **Step 1: Add WebhookRouting interface and update Project**

In `frontend/src/lib/api.ts`, add the interface before `Project` and update:

```typescript
export interface WebhookRouting {
  ado: string[];
  pagerduty: string[];
  salesforce: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  tag_filter: TagFilter | null;
  webhook_write_back: Record<string, boolean | null> | null;
  webhook_routing: WebhookRouting | null;
  created_at: string;
}
```

- [ ] **Step 2: Update createProject and updateProject signatures**

Add `webhook_routing` to the accepted payload types:

```typescript
  createProject(data: {
    name: string;
    description?: string;
    tag_filter?: TagFilter | null;
    webhook_routing?: WebhookRouting | null;
  }): Promise<Project> {
    return request('/api/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateProject(id: string, data: Partial<{
    name: string;
    description: string;
    tag_filter: TagFilter | null;
    webhook_routing: WebhookRouting | null;
  }>): Promise<Project> {
    return request(`/api/projects/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -s --no-verify -m "feat: add WebhookRouting to TypeScript Project types"
```

---

### Task 6: Add Webhook Routing UI to Project Modal

**Files:**
- Modify: `frontend/src/components/Projects.tsx:175-358` (ProjectModal component)

- [ ] **Step 1: Add WebhookRouting state to ProjectModal**

Import `WebhookRouting` from api.ts. Add state after the existing `webhooks` state (line ~195):

```typescript
import { api, type Project, type Instance, type TagFilter, type WebhookInfo, type WebhookRouting } from '../lib/api'

// Inside ProjectModal, after line 195:
const [webhookRouting, setWebhookRouting] = useState<WebhookRouting>(
  project?.webhook_routing ?? { ado: [], pagerduty: [], salesforce: [] }
)
```

- [ ] **Step 2: Include webhook_routing in save payload**

Update the `handleSave` payload (around line 210):

```typescript
      const payload = {
        name: name.trim(),
        description: description.trim(),
        tag_filter: tagFilter,
        webhook_write_back: Object.keys(webhookWriteBack).length > 0 ? webhookWriteBack : null,
        webhook_routing: (webhookRouting.ado.length || webhookRouting.pagerduty.length || webhookRouting.salesforce.length)
          ? webhookRouting
          : null,
      }
```

- [ ] **Step 3: Add WebhookRoutingEditor UI section**

Add a `WebhookRoutingEditor` inline component (or JSX block) inside ProjectModal's form, after the Write-Back Settings section (before the error display, around line 337). This renders three tag-input rows:

```tsx
          {/* ── Webhook Routing ──────────────────────────────────── */}
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">
              Webhook Routing
            </label>
            <p className="text-xs text-pdi-slate mb-3">
              Map incoming webhook events to this project. Events that don't match any project use global integrations.
            </p>
            <div className="space-y-3">
              {([
                { key: 'ado' as const, label: 'ADO Projects', placeholder: 'e.g. PDI Dispatch' },
                { key: 'pagerduty' as const, label: 'PagerDuty Services', placeholder: 'e.g. checkout-api' },
                { key: 'salesforce' as const, label: 'Salesforce Accounts', placeholder: 'e.g. Acme Corp' },
              ]).map(({ key, label, placeholder }) => (
                <div key={key} className="py-2 px-3 bg-gray-50 rounded-lg">
                  <span className="text-xs font-medium text-pdi-slate block mb-1.5">{label}</span>
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {webhookRouting[key].map((val, idx) => (
                      <span
                        key={idx}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-pdi-sky/10 text-pdi-indigo border border-pdi-sky/30 rounded-full"
                      >
                        {val}
                        <button
                          type="button"
                          onClick={() => {
                            const next = { ...webhookRouting, [key]: webhookRouting[key].filter((_, i) => i !== idx) }
                            setWebhookRouting(next)
                          }}
                          className="text-pdi-slate hover:text-pdi-orange ml-0.5"
                        >
                          x
                        </button>
                      </span>
                    ))}
                  </div>
                  <input
                    type="text"
                    placeholder={placeholder}
                    className="w-full text-xs border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-pdi-sky"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        const input = e.currentTarget
                        const value = input.value.trim()
                        if (value && !webhookRouting[key].includes(value)) {
                          setWebhookRouting({ ...webhookRouting, [key]: [...webhookRouting[key], value] })
                          input.value = ''
                        }
                      }
                    }}
                  />
                </div>
              ))}
            </div>
          </div>
```

- [ ] **Step 4: Verify TypeScript compiles and lint**

```bash
cd frontend && npx tsc --noEmit && npx eslint src/components/Projects.tsx
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Projects.tsx
git commit -s --no-verify -m "feat: add webhook routing UI to project modal"
```

---

### Task 7: Deploy and Test End-to-End

**Files:** No code changes — deployment and manual verification.

- [ ] **Step 1: Build and deploy to dev**

Use the `/ship` skill or manual deployment workflow to deploy the changes to `holmesgpt.dev.platform.pditechnologies.com`.

- [ ] **Step 2: Create/edit a project with ADO routing**

In the Holmes UI, edit the target project and add `PDI Dispatch` (or whatever the ADO team project name is) to the ADO Projects routing field.

- [ ] **Step 3: Trigger ADO webhook test**

In ADO Project Settings > Service hooks, use the "Test" button on the existing webhook to send a test `workitem.created` event.

- [ ] **Step 4: Verify investigation is saved with correct project_id**

Check the Holmes UI investigation history — the new investigation should show the correct project association.

- [ ] **Step 5: Verify fallback behavior**

Send a webhook event from an ADO project NOT mapped to any Holmes project. The investigation should save with `project_id=""` (global).
