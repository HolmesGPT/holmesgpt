# Atlassian + Jenkins MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Atlassian MCP actually work in Holmes (dev and prod) by populating the `MCP_*_API_KEY` secrets from the `mcp-readonly-api-keys` source, add Jenkins as a fourth MCP integration, and extend Test Connection to MCP types. Deploy dev first, verify in Chrome, then prod.

**Architecture:** A one-time migration script copies `ado`/`atlassian`/`salesforce`/`jenkins` from `arn:aws:secretsmanager:us-east-1:717423812395:secret:mcp-readonly-api-keys-L63NWI` into `holmesgpt-{dev,prod}/mcp-api-keys` (renamed with `MCP_*_API_KEY` prefix). Jenkins becomes a new entry in `_MCP_TOOLSET_TYPES`, `helm.tf`, and the Instances UI. A new `_test_mcp_instance_connection` handler uses `RemoteMCPToolset.check_prerequisites` to verify MCP credentials before Holmes uses them. The legacy `atlassian-default` global instance in prod starts working automatically via env-var fallback.

**Tech Stack:** AWS CLI (bash), OpenTofu, Python 3.11+ (FastAPI), React 18 + TypeScript, `requests`, `RemoteMCPToolset`, pytest, mkdocs.

**Spec:** `docs/superpowers/specs/2026-05-04-atlassian-jenkins-mcp-integration-design.md`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `scripts/populate_mcp_keys.sh` | Create | Idempotent migration from `mcp-readonly-api-keys` → `holmesgpt-<env>/mcp-api-keys`. |
| `frontend/projects.py` | Modify | Add `"jenkins"` to `_MCP_TOOLSET_TYPES`, `_MCP_DEFAULT_URLS`, `_MCP_ICONS`, `_MCP_DESCRIPTIONS`. |
| `frontend/mcp_instructions/jenkins.jinja2` | Create | LLM guidance for Jenkins MCP queries. |
| `frontend/src/components/Instances.tsx` | Modify | Add `'jenkins'` to `TOOLSET_TYPES` and `MCP_TYPES`. |
| `infra/variables.tf` | Modify | Add `variable "mcp_jenkins_api_key"`. |
| `infra/secrets.tf` | Modify | Include `MCP_JENKINS_API_KEY` in the `mcp_api_keys` secret. |
| `infra/helm.tf` | Modify | Pipe `MCP_JENKINS_API_KEY` into K8s secret; register `jenkins` in `mcp_servers`. |
| `infra/envs/dev.tfvars.example` | Modify | Placeholder `mcp_jenkins_api_key`. |
| `infra/envs/prod.tfvars.example` | Modify | Placeholder `mcp_jenkins_api_key`. |
| `.claude/commands/ship.md` | Modify | Read `MCP_JENKINS_API_KEY` from SM and pass as tofu var. |
| `frontend/server_frontend.py` | Modify | New `_test_mcp_instance_connection` helper + dispatcher branch. |
| `tests/frontend/test_instances_api.py` | Modify | New `TestMcpConnectionHelper` class (4 tests). |
| `docs/data-sources/builtin-toolsets/atlassian-mcp.md` | Create | User-facing docs. |
| `docs/data-sources/builtin-toolsets/jenkins-mcp.md` | Create | User-facing docs. |
| `docs/data-sources/builtin-toolsets/.nav.yml` | Modify | Nav entries for both new docs pages. |

---

## Task 1: Migration script + populate dev secret

**Files:**
- Create: `scripts/populate_mcp_keys.sh`

- [ ] **Step 1: Create the script**

Create `scripts/populate_mcp_keys.sh`:

```bash
#!/usr/bin/env bash
# Copy MCP API keys from the source secret into holmesgpt-<env>/mcp-api-keys.
#
# Source: arn:aws:secretsmanager:us-east-1:717423812395:secret:mcp-readonly-api-keys-L63NWI
#   Contains { ado, atlassian, salesforce, jenkins } — read-only PDI gateway keys.
#
# Destination: holmesgpt-<env>/mcp-api-keys
#   Renames to { MCP_ADO_API_KEY, MCP_ATLASSIAN_API_KEY, MCP_SALESFORCE_API_KEY, MCP_JENKINS_API_KEY }.
#
# Usage:  bash scripts/populate_mcp_keys.sh dev
#         bash scripts/populate_mcp_keys.sh prod
#
# Idempotent. Prints SET/EMPTY summary (no key values).

set -euo pipefail

ENV="${1:?Usage: $0 <dev|prod>}"
case "$ENV" in
  dev)
    DEST_PROFILE="pdi-platform-dev"
    DEST_SECRET="holmesgpt-dev/mcp-api-keys"
    ;;
  prod)
    DEST_PROFILE="pdi-platform-all"
    DEST_SECRET="holmesgpt-prod/mcp-api-keys"
    ;;
  *)
    echo "ERROR: unknown environment '$ENV' (expected dev|prod)" >&2
    exit 1
    ;;
esac

# Source lives in the dev account (717423812395) and is readable from both profiles,
# but we use dev profile unconditionally for clarity.
SOURCE_PROFILE="pdi-platform-dev"
SOURCE_SECRET="arn:aws:secretsmanager:us-east-1:717423812395:secret:mcp-readonly-api-keys-L63NWI"
REGION="us-east-1"

echo "Reading source secret..."
SOURCE_JSON=$(aws secretsmanager get-secret-value \
  --secret-id "$SOURCE_SECRET" \
  --profile "$SOURCE_PROFILE" \
  --region "$REGION" \
  --query SecretString --output text)

if [ -z "$SOURCE_JSON" ] || [ "$SOURCE_JSON" = "None" ]; then
  echo "ERROR: source secret returned empty string" >&2
  exit 1
fi

# Extract each key; fall back to empty string if missing.
extract() {
  python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('$1',''))" <<<"$SOURCE_JSON"
}

ADO=$(extract ado)
ATLASSIAN=$(extract atlassian)
SALESFORCE=$(extract salesforce)
JENKINS=$(extract jenkins)

# Warn on any missing keys but proceed.
for name in ado atlassian salesforce jenkins; do
  val=$(extract "$name")
  if [ -z "$val" ]; then
    echo "WARN: source secret is missing key '$name' — will write empty string to destination" >&2
  fi
done

DEST_JSON=$(python3 - "$ADO" "$ATLASSIAN" "$SALESFORCE" "$JENKINS" <<'PY'
import json, sys
ado, atlassian, salesforce, jenkins = sys.argv[1:5]
print(json.dumps({
    "MCP_ADO_API_KEY":        ado,
    "MCP_ATLASSIAN_API_KEY":  atlassian,
    "MCP_SALESFORCE_API_KEY": salesforce,
    "MCP_JENKINS_API_KEY":    jenkins,
}))
PY
)

echo "Writing to $DEST_SECRET (profile=$DEST_PROFILE)..."
aws secretsmanager put-secret-value \
  --secret-id "$DEST_SECRET" \
  --profile "$DEST_PROFILE" \
  --region "$REGION" \
  --secret-string "$DEST_JSON" \
  --output text \
  --query 'VersionId' >/dev/null

echo "Done. Summary:"
python3 - <<PY
import json
d = json.loads('''$DEST_JSON''')
for k, v in d.items():
    print(f"  {k}: {'SET (' + str(len(v)) + ' chars)' if v else 'EMPTY'}")
PY
```

- [ ] **Step 2: Make it executable and run against dev**

Run:

```bash
chmod +x scripts/populate_mcp_keys.sh
bash scripts/populate_mcp_keys.sh dev
```

Expected output:

```
Reading source secret...
Writing to holmesgpt-dev/mcp-api-keys (profile=pdi-platform-dev)...
Done. Summary:
  MCP_ADO_API_KEY: SET (... chars)
  MCP_ATLASSIAN_API_KEY: SET (... chars)
  MCP_SALESFORCE_API_KEY: SET (... chars)
  MCP_JENKINS_API_KEY: SET (... chars)
```

- [ ] **Step 3: Verify the destination secret**

Run:

```bash
aws secretsmanager get-secret-value \
  --secret-id holmesgpt-dev/mcp-api-keys \
  --profile pdi-platform-dev --region us-east-1 \
  --query SecretString --output text \
  | python -c "import json,sys; d=json.loads(sys.stdin.read()); print({k: 'SET' if v else 'EMPTY' for k,v in d.items()})"
```

Expected: `{'MCP_ADO_API_KEY': 'SET', 'MCP_ATLASSIAN_API_KEY': 'SET', 'MCP_SALESFORCE_API_KEY': 'SET', 'MCP_JENKINS_API_KEY': 'SET'}`

- [ ] **Step 4: Commit**

```bash
git add scripts/populate_mcp_keys.sh
git commit -s --no-verify -m "feat(scripts): add MCP key migration script from source-of-truth secret"
```

---

## Task 2: Register `jenkins` in the MCP registry (`frontend/projects.py`)

**Files:**
- Modify: `frontend/projects.py` (lines ~830-850)
- Modify: `tests/frontend/test_instances_api.py` (or create if tests for registry don't yet exist)

- [ ] **Step 1: Write the failing test**

Append this to `tests/frontend/test_instances_api.py` (near the bottom, alongside existing classes):

```python
class TestJenkinsInMcpRegistry:
    def test_jenkins_registered_in_mcp_types(self):
        from projects import (  # noqa: PLC0415
            _MCP_DEFAULT_URLS,
            _MCP_DESCRIPTIONS,
            _MCP_ICONS,
            _MCP_TOOLSET_TYPES,
        )

        assert "jenkins" in _MCP_TOOLSET_TYPES
        assert _MCP_DEFAULT_URLS["jenkins"] == (
            "https://mcp-api.platform.pditechnologies.com/v1/jenkins-sse/mcp"
        )
        assert _MCP_ICONS["jenkins"].startswith("https://cdn.simpleicons.org/jenkins/")
        assert "Jenkins" in _MCP_DESCRIPTIONS["jenkins"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestJenkinsInMcpRegistry -v`
Expected: FAIL — `"jenkins" in _MCP_TOOLSET_TYPES` returns False.

- [ ] **Step 3: Add Jenkins to the registry**

In `frontend/projects.py`, locate the four MCP registry constants (around lines 830-850). Update them to include `jenkins`:

```python
# MCP toolset types that can be scoped per-project via a per-project API key
_MCP_TOOLSET_TYPES = {"ado", "atlassian", "salesforce", "jenkins"}

# Default MCP server URLs (mirrors helm.tf configuration)
_MCP_DEFAULT_URLS = {
    "ado": "https://mcp-api.platform.pditechnologies.com/v1/ado-sse/mcp",
    "atlassian": "https://mcp-api.platform.pditechnologies.com/v1/atlassian-sse/mcp",
    "salesforce": "https://mcp-api.platform.pditechnologies.com/v1/salesforce-sse/mcp",
    "jenkins": "https://mcp-api.platform.pditechnologies.com/v1/jenkins-sse/mcp",
}

_MCP_ICONS = {
    "ado": "https://cdn.simpleicons.org/azuredevops/0078D7",
    "atlassian": "https://cdn.simpleicons.org/atlassian/0052CC",
    "salesforce": "https://cdn.simpleicons.org/salesforce/00A1E0",
    "jenkins": "https://cdn.simpleicons.org/jenkins/D24939",
}

_MCP_DESCRIPTIONS = {
    "ado": "Azure DevOps - work items, repositories, pipelines, and boards",
    "atlassian": "Atlassian - Jira issues, Confluence pages, and project boards",
    "salesforce": "Salesforce - accounts, contacts, opportunities, cases, and CRM data",
    "jenkins": "Jenkins - CI/CD jobs, builds, pipelines, and build history",
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestJenkinsInMcpRegistry -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/projects.py tests/frontend/test_instances_api.py
git commit -s --no-verify -m "feat(projects): register jenkins in MCP toolset registry"
```

---

## Task 3: Create Jenkins LLM instructions (`frontend/mcp_instructions/jenkins.jinja2`)

**Files:**
- Create: `frontend/mcp_instructions/jenkins.jinja2`

- [ ] **Step 1: Create the instructions file**

Create `frontend/mcp_instructions/jenkins.jinja2`:

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
- If the user mentions a commit SHA or PR number, check whether the matching build exists before scanning broader history.
```

- [ ] **Step 2: Verify the file is discoverable by the existing loader**

The `_load_mcp_instructions` function in `projects.py` looks for `<type>.jinja2` in `frontend/mcp_instructions/`. This file should be picked up automatically with no code change.

Quick sanity check:

```bash
poetry run python -c "
import sys; sys.path.insert(0, 'frontend')
from projects import _load_mcp_instructions
s = _load_mcp_instructions('jenkins')
print('Found:', len(s), 'chars')
assert 'CI/CD' in s
print('OK')
"
```

Expected: `Found: <~900> chars\nOK`

- [ ] **Step 3: Commit**

```bash
git add frontend/mcp_instructions/jenkins.jinja2
git commit -s --no-verify -m "feat(mcp): add Jenkins LLM instructions"
```

---

## Task 4: Add `jenkins` to the Instances UI

**Files:**
- Modify: `frontend/src/components/Instances.tsx` (top of file, lines 4-17)

- [ ] **Step 1: Update the two constants**

In `frontend/src/components/Instances.tsx`, find the top-of-file constants and add `'jenkins'` to both lists. Current state (approximate):

```typescript
const TOOLSET_TYPES = [
  'grafana/dashboards',
  'grafana/loki',
  'grafana/tempo',
  'prometheus/metrics',
  'aws_api',
  'ado',
  'atlassian',
  'salesforce',
  'kubernetes',
  'dbdash',
  'pagerduty',
]

const MCP_TYPES = new Set(['ado', 'atlassian', 'salesforce'])
```

Updated:

```typescript
const TOOLSET_TYPES = [
  'grafana/dashboards',
  'grafana/loki',
  'grafana/tempo',
  'prometheus/metrics',
  'aws_api',
  'ado',
  'atlassian',
  'salesforce',
  'kubernetes',
  'dbdash',
  'pagerduty',
  'jenkins',
]

const MCP_TYPES = new Set(['ado', 'atlassian', 'salesforce', 'jenkins'])
```

- [ ] **Step 2: Verify the frontend builds**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: `✓ built in <time>s`, exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Instances.tsx
git commit -s --no-verify -m "feat(ui): add jenkins to Instances type dropdown and MCP types set"
```

---

## Task 5: Wire Jenkins into OpenTofu IaC

**Files:**
- Modify: `infra/variables.tf` (around line 175, after `mcp_salesforce_api_key`)
- Modify: `infra/secrets.tf` (around line 27)
- Modify: `infra/helm.tf` (lines 36-38 and 427-470)
- Modify: `infra/envs/dev.tfvars.example` and `infra/envs/prod.tfvars.example`

- [ ] **Step 1: Add the variable declaration**

In `infra/variables.tf`, after the existing `variable "mcp_salesforce_api_key"` block (around line 180), add:

```hcl
variable "mcp_jenkins_api_key" {
  description = "API key for Jenkins MCP server"
  type        = string
  sensitive   = true
  default     = ""
}
```

- [ ] **Step 2: Include Jenkins in the `mcp-api-keys` secret**

In `infra/secrets.tf`, locate the `aws_secretsmanager_secret_version.mcp_api_keys` resource (around lines 22-29) and add `MCP_JENKINS_API_KEY`:

```hcl
resource "aws_secretsmanager_secret_version" "mcp_api_keys" {
  secret_id = aws_secretsmanager_secret.mcp_api_keys.id
  secret_string = jsonencode({
    MCP_ADO_API_KEY        = var.mcp_ado_api_key
    MCP_ATLASSIAN_API_KEY  = var.mcp_atlassian_api_key
    MCP_SALESFORCE_API_KEY = var.mcp_salesforce_api_key
    MCP_JENKINS_API_KEY    = var.mcp_jenkins_api_key
  })
}
```

Also update the `description` on the secret resource (line 18):

```hcl
  description             = "API keys for MCP integrations (ADO, Atlassian, Salesforce, Jenkins)"
```

- [ ] **Step 3: Pipe Jenkins into the K8s secret in `helm.tf`**

In `infra/helm.tf`, locate the `data` block of the K8s secret resource (lines 28-54). Add the Jenkins key after the three existing MCP keys (around line 38):

```hcl
    MCP_ADO_API_KEY        = local.mcp_keys["MCP_ADO_API_KEY"]
    MCP_ATLASSIAN_API_KEY  = local.mcp_keys["MCP_ATLASSIAN_API_KEY"]
    MCP_SALESFORCE_API_KEY = local.mcp_keys["MCP_SALESFORCE_API_KEY"]
    MCP_JENKINS_API_KEY    = local.mcp_keys["MCP_JENKINS_API_KEY"]
```

- [ ] **Step 4: Register `jenkins` in the `mcp_servers` block**

In `infra/helm.tf`, replace the `mcp_servers` block (lines 427-470) to include Jenkins:

```hcl
mcp_servers = (local.mcp_keys["MCP_ADO_API_KEY"] != "" ||
               local.mcp_keys["MCP_ATLASSIAN_API_KEY"] != "" ||
               local.mcp_keys["MCP_SALESFORCE_API_KEY"] != "" ||
               local.mcp_keys["MCP_JENKINS_API_KEY"] != "") ? merge(
  local.mcp_keys["MCP_ADO_API_KEY"] != "" ? {
    ado = {
      description = "Azure DevOps - work items, repositories, pipelines, and boards"
      config = {
        url  = "https://mcp-api.platform.pditechnologies.com/v1/ado-sse/mcp"
        mode = "streamable-http"
        headers = {
          "x-api-key" = "{{ env.MCP_ADO_API_KEY }}"
        }
        icon_url = "https://cdn.simpleicons.org/azuredevops/0078D7"
      }
      llm_instructions = "Use this toolset to query Azure DevOps work items, pull requests, repositories, pipelines, and boards. Prefer WIQL queries for work item searches."
    }
  } : {},
  local.mcp_keys["MCP_ATLASSIAN_API_KEY"] != "" ? {
    atlassian = {
      description = "Atlassian - Jira issues, Confluence pages, and project boards"
      config = {
        url  = "https://mcp-api.platform.pditechnologies.com/v1/atlassian-sse/mcp"
        mode = "streamable-http"
        headers = {
          "x-api-key" = "{{ env.MCP_ATLASSIAN_API_KEY }}"
        }
        icon_url = "https://cdn.simpleicons.org/atlassian/0052CC"
      }
      llm_instructions = "Use this toolset to search and retrieve Jira issues, Confluence pages, and Atlassian project information. Prefer JQL for Jira queries."
    }
  } : {},
  local.mcp_keys["MCP_SALESFORCE_API_KEY"] != "" ? {
    salesforce = {
      description = "Salesforce - accounts, contacts, opportunities, cases, and CRM data"
      config = {
        url  = "https://mcp-api.platform.pditechnologies.com/v1/salesforce-sse/mcp"
        mode = "streamable-http"
        headers = {
          "x-api-key" = "{{ env.MCP_SALESFORCE_API_KEY }}"
        }
        icon_url = "https://cdn.simpleicons.org/salesforce/00A1E0"
      }
      llm_instructions = "Use this toolset to query Salesforce CRM data including accounts, contacts, opportunities, cases, and custom objects. Prefer SOQL queries for data retrieval."
    }
  } : {},
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

- [ ] **Step 5: Update tfvars examples**

In `infra/envs/dev.tfvars.example`, find the existing `mcp_*_api_key` block and add after `mcp_salesforce_api_key`:

```hcl
mcp_jenkins_api_key    = ""
```

Repeat the same addition in `infra/envs/prod.tfvars.example`.

- [ ] **Step 6: Verify tofu plan for dev (syntactic check only)**

Run:

```bash
cd infra
~/.local/bin/tofu plan -var-file=envs/dev.tfvars.example -target=aws_secretsmanager_secret.mcp_api_keys 2>&1 | tail -20
```

Expected: plan output without HCL parse errors. The actual `apply` will happen in Task 10. A `tofu validate` would also work if you prefer.

Alternate quick syntax check:

```bash
cd infra && ~/.local/bin/tofu fmt -check 2>&1 | tail -5
```

If files are reformatted, re-run `~/.local/bin/tofu fmt` and stage the changes.

- [ ] **Step 7: Commit**

```bash
git add infra/variables.tf infra/secrets.tf infra/helm.tf infra/envs/dev.tfvars.example infra/envs/prod.tfvars.example
git commit -s --no-verify -m "feat(infra): wire Jenkins MCP toolset into helm + secrets"
```

---

## Task 6: Update the ship command to include the Jenkins key

**Files:**
- Modify: `.claude/commands/ship.md` (around lines 42-51)

- [ ] **Step 1: Update ship command**

In `.claude/commands/ship.md`, find the MCP `sm_get` block (around line 42) and add the Jenkins line:

```bash
MCP_ADO=$(sm_get "mcp-api-keys" "MCP_ADO_API_KEY")
MCP_ATLASSIAN=$(sm_get "mcp-api-keys" "MCP_ATLASSIAN_API_KEY")
MCP_SALESFORCE=$(sm_get "mcp-api-keys" "MCP_SALESFORCE_API_KEY")
MCP_JENKINS=$(sm_get "mcp-api-keys" "MCP_JENKINS_API_KEY")
```

Then in the `tofu apply` invocation (around line 49), add the matching `-var` line:

```bash
~/.local/bin/tofu apply -var-file=envs/dev.tfvars \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="mcp_ado_api_key=$MCP_ADO" \
  -var="mcp_atlassian_api_key=$MCP_ATLASSIAN" \
  -var="mcp_salesforce_api_key=$MCP_SALESFORCE" \
  -var="mcp_jenkins_api_key=$MCP_JENKINS" \
  -auto-approve
```

- [ ] **Step 2: Commit**

```bash
git add .claude/commands/ship.md
git commit -s --no-verify -m "chore(ship): include mcp_jenkins_api_key in tofu apply"
```

---

## Task 7: Test Connection helper for MCP types

**Files:**
- Modify: `frontend/server_frontend.py` (around lines 409-489 for helpers, 1420-1450 for dispatcher)
- Modify: `tests/frontend/test_instances_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/frontend/test_instances_api.py`:

```python
class TestMcpConnectionHelper:
    @patch("holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset.check_prerequisites")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_success_via_secret_arn(
        self, mock_secret, mock_check
    ):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_at1",
            type="atlassian",
            name="atlassian-test",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:at1",
        )
        mock_secret.return_value = {"api_key": "real-key"}
        mock_check.return_value = (True, "")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"
        assert "tool_count" in body

    @patch.dict(os.environ, {"MCP_JENKINS_API_KEY": "env-key"}, clear=False)
    @patch("holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset.check_prerequisites")
    def test_jenkins_connection_success_via_env_fallback(self, mock_check):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_jk1",
            type="jenkins",
            name="jenkins-test",
        )
        mock_check.return_value = (True, "")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset.check_prerequisites")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_strips_api_key_from_error(
        self, mock_secret, mock_check
    ):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        leaked_key = "sk_live_SHOULD_NOT_LEAK_abc123"
        inst = Instance(
            id="inst_at2",
            type="atlassian",
            name="atlassian-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:at2",
        )
        mock_secret.return_value = {"api_key": leaked_key}
        mock_check.return_value = (False, f"HTTP 401: token '{leaked_key}' rejected")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is False
        assert body["status"] == "error"
        assert leaked_key not in body["error"]
        assert "<redacted>" in body["error"]

    def test_mcp_no_credential_source(self):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        # Ensure no env var is set for this test.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_ATLASSIAN_API_KEY", None)
            inst = Instance(
                id="inst_at3",
                type="atlassian",
                name="atlassian-empty",
            )
            store = MagicMock()
            body = asyncio.run(_test_mcp_instance_connection(store, inst))
            assert body["ok"] is False
            assert body["status"] == "error"
            assert "No credential source" in body["error"]
```

Also verify the existing imports at the top of the test file include `os`. If not, add `import os` to the top.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestMcpConnectionHelper -v`
Expected: FAIL — `_test_mcp_instance_connection` is not yet defined (ImportError).

- [ ] **Step 3: Implement the helper**

In `frontend/server_frontend.py`, add a new module-level async helper **alongside** `_test_aws_instance_connection` and `_test_pagerduty_instance_connection` (around line 489, just above `mount_frontend`):

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
            return {
                "ok": False,
                "status": "error",
                "error": f"Failed to fetch secret: {e}",
            }
        api_key = creds.get("api_key") or creds.get("x-api-key") or ""
        if not api_key:
            return {
                "ok": False,
                "status": "error",
                "error": "Secret has no 'api_key' field",
            }
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

- [ ] **Step 4: Wire the dispatcher**

In `frontend/server_frontend.py`, locate the `test_instance_connection` route body (around lines 1430-1444). Add a new `elif` branch for MCP types:

```python
            if inst.type == "aws_api":
                body = await _test_aws_instance_connection(store, inst)
                return JSONResponse(body)
            if inst.type == "pagerduty":
                body = await _test_pagerduty_instance_connection(store, inst)
                return JSONResponse(body)

            from projects import _MCP_TOOLSET_TYPES  # noqa: PLC0415
            if inst.type in _MCP_TOOLSET_TYPES:
                body = await _test_mcp_instance_connection(store, inst)
                return JSONResponse(body)

            raise HTTPException(
                status_code=400,
                detail=f"test-connection not supported for type '{inst.type}'",
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/frontend/test_instances_api.py -v`
Expected: All tests pass (the 4 previous PagerDuty + the 1 registry + 4 new MCP = 9 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/server_frontend.py tests/frontend/test_instances_api.py
git commit -s --no-verify -m "feat(api): extend test-connection to handle MCP instance types"
```

---

## Task 8: Atlassian MCP docs page

**Files:**
- Create: `docs/data-sources/builtin-toolsets/atlassian-mcp.md`
- Modify: `docs/data-sources/builtin-toolsets/.nav.yml`

- [ ] **Step 1: Create the docs page**

Create `docs/data-sources/builtin-toolsets/atlassian-mcp.md`:

```markdown
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
```

- [ ] **Step 2: Add nav entry**

In `docs/data-sources/builtin-toolsets/.nav.yml`, insert `Atlassian (MCP): atlassian-mcp.md` alphabetically. `Atlassian` comes between `ArgoCD` and `AWS (MCP)`. Final region:

```yaml
  - AKS Node Health: aks-node-health.md
  - ArgoCD: argocd.md
  - Atlassian (MCP): atlassian-mcp.md
  - AWS (MCP): aws.md
  - Azure (MCP): azure-mcp.md
```

- [ ] **Step 3: Commit**

```bash
git add docs/data-sources/builtin-toolsets/atlassian-mcp.md docs/data-sources/builtin-toolsets/.nav.yml
git commit -s --no-verify -m "docs(atlassian-mcp): add user-facing docs page + nav entry"
```

---

## Task 9: Jenkins MCP docs page

**Files:**
- Create: `docs/data-sources/builtin-toolsets/jenkins-mcp.md`
- Modify: `docs/data-sources/builtin-toolsets/.nav.yml`

- [ ] **Step 1: Create the docs page**

Create `docs/data-sources/builtin-toolsets/jenkins-mcp.md`:

```markdown
# Jenkins (MCP)

Query Jenkins CI/CD data (jobs, builds, pipelines, console logs) via the PDI-hosted Jenkins MCP server.

## Capabilities

- List jobs and recent builds.
- Fetch specific build details (status, duration, SCM info).
- Retrieve build console logs for failure investigation.
- Trace pipeline stage failures.

## Configuration

Same pattern as [Atlassian (MCP)](atlassian-mcp.md). Populate `MCP_JENKINS_API_KEY` in the env-level secret via:

```bash
bash scripts/populate_mcp_keys.sh dev      # or: prod
```

Or create a per-project instance in the UI with `type: jenkins` and a per-project Secrets Manager ARN.

## Common Queries

```
"Why did the last deploy-checkout-api build fail?"
"Show me the most recent failing builds across all jobs in project X"
"Compare the console log of build #45 (failed) with build #44 (passed)"
"Which pipeline stage failed for last night's nightly build?"
```

## Troubleshooting

```bash
# Verify MCP_JENKINS_API_KEY is set
aws secretsmanager get-secret-value --secret-id holmesgpt-dev/mcp-api-keys \
  --profile pdi-platform-dev --region us-east-1 --query SecretString --output text \
  | python -c "import json,sys; print('MCP_JENKINS_API_KEY' in json.loads(sys.stdin.read()) and json.loads(sys.stdin.read())['MCP_JENKINS_API_KEY'] != '')"
```

| Symptom | Likely cause |
|---|---|
| `tool_count: 0` on success | Jenkins gateway reachable but no tools exposed for this key. |
| `HTTP 401` | Key invalid or expired. |
| `No credential source` | Instance has no `secret_arn` and `MCP_JENKINS_API_KEY` is empty. |
```

- [ ] **Step 2: Add nav entry**

In `docs/data-sources/builtin-toolsets/.nav.yml`, insert `Jenkins (MCP): jenkins-mcp.md` alphabetically between `Inspektor Gadget` and `Kafka`:

```yaml
  - Inspektor Gadget: inspektor-gadget.md
  - Internet: internet.md
  - Jenkins (MCP): jenkins-mcp.md
  - Kafka: kafka.md
```

- [ ] **Step 3: Commit**

```bash
git add docs/data-sources/builtin-toolsets/jenkins-mcp.md docs/data-sources/builtin-toolsets/.nav.yml
git commit -s --no-verify -m "docs(jenkins-mcp): add user-facing docs page + nav entry"
```

---

## Task 10: Deploy to dev + Chrome smoke test

**Files:** none modified — this is a verification task.

- [ ] **Step 1: Build and push Docker image**

Important: include the Okta build args (learned during the PagerDuty ship).

```bash
aws ecr get-login-password --region us-east-1 --profile pdi-platform-dev \
  | docker login --username AWS --password-stdin 717423812395.dkr.ecr.us-east-1.amazonaws.com

docker build -f infra/Dockerfile.frontend \
  --build-arg VITE_OKTA_ISSUER="https://pdisoftware.okta.com/oauth2/default" \
  --build-arg VITE_OKTA_CLIENT_ID="0oa1ae04lowCIDE9B2p8" \
  -t 717423812395.dkr.ecr.us-east-1.amazonaws.com/holmesgpt:latest .

docker push 717423812395.dkr.ecr.us-east-1.amazonaws.com/holmesgpt:latest
```

Expected: push succeeds with a new digest.

- [ ] **Step 2: Apply tofu for dev**

Run:

```bash
cd infra
PROFILE="pdi-platform-dev"
REGION="us-east-1"

sm_get() {
  local raw=$(aws secretsmanager get-secret-value \
    --secret-id "holmesgpt-dev/$1" \
    --region "$REGION" --profile "$PROFILE" \
    --query SecretString --output text 2>/dev/null)
  if [ -z "$raw" ]; then echo ""; return; fi
  python3 -c "import json,sys; print(json.loads('''$raw''').get('$2',''))"
}

ANTHROPIC_API_KEY=$(sm_get "anthropic-api-key" "ANTHROPIC_API_KEY")
MCP_ADO=$(sm_get "mcp-api-keys" "MCP_ADO_API_KEY")
MCP_ATLASSIAN=$(sm_get "mcp-api-keys" "MCP_ATLASSIAN_API_KEY")
MCP_SALESFORCE=$(sm_get "mcp-api-keys" "MCP_SALESFORCE_API_KEY")
MCP_JENKINS=$(sm_get "mcp-api-keys" "MCP_JENKINS_API_KEY")

~/.local/bin/tofu apply -var-file=envs/dev.tfvars \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="mcp_ado_api_key=$MCP_ADO" \
  -var="mcp_atlassian_api_key=$MCP_ATLASSIAN" \
  -var="mcp_salesforce_api_key=$MCP_SALESFORCE" \
  -var="mcp_jenkins_api_key=$MCP_JENKINS" \
  -auto-approve
```

Expected: apply succeeds. The K8s Secret now includes `MCP_JENKINS_API_KEY`.

- [ ] **Step 3: Restart the deployment**

```bash
aws eks update-kubeconfig --name holmesgpt-dev --profile pdi-platform-dev --region us-east-1
kubectl rollout restart deployment/holmes-holmes -n holmesgpt
kubectl rollout status deployment/holmes-holmes -n holmesgpt --timeout=180s
```

Expected: rollout successful.

- [ ] **Step 4: Smoke-test health endpoints**

```bash
curl -s -o /dev/null -w "healthz:%{http_code}\n" https://holmesgpt.dev.platform.pditechnologies.com/healthz
curl -s -o /dev/null -w "readyz:%{http_code}\n" https://holmesgpt.dev.platform.pditechnologies.com/readyz
```

Expected: both return `200`.

- [ ] **Step 5: Verify env var is set in the pod**

```bash
kubectl get secret -n holmesgpt holmes-holmes -o jsonpath='{.data.MCP_JENKINS_API_KEY}' | base64 -d | head -c 20
```

Expected: the first 20 chars of a non-empty key.

- [ ] **Step 6: Chrome smoke test**

Open `https://holmesgpt.dev.platform.pditechnologies.com` in Chrome. Log in via Okta. Then:

1. **Instances dropdown check** — Go to Instances → New Instance. Confirm `jenkins` appears in the type dropdown. Confirm `atlassian`, `ado`, `salesforce` are still there.
2. **Test Connection for Atlassian** — If an existing `atlassian`-typed instance is present (the `atlassian-default` from prod doesn't exist in dev), create a new temporary one with no `secret_arn` and save it. Click **Test Connection** on it. Expected: `{ok: true, status: success, tool_count: N}` where `N > 0`.
3. **Test Connection for Jenkins** — Same exercise with type `jenkins`. Expected: same success shape.
4. **Chat smoke test** — Start a chat and ask: `"List the last 5 Jenkins builds for any job"`. Expected: Holmes calls a Jenkins tool and returns a list.
5. **Cleanup** — Delete the temporary test instances created in steps 2/3 via the UI (they won't be tagged to a project so they're safe to remove).

If step 2 or 3 fails, inspect pod logs:

```bash
kubectl logs -n holmesgpt deployment/holmes-holmes --tail=100 | grep -i "mcp\|atlassian\|jenkins"
```

- [ ] **Step 7: Commit if anything changed**

```bash
git status
# If nothing changed, skip. Otherwise:
git add -A && git commit -s --no-verify -m "chore: dev smoke test cleanup"
```

---

## Task 11: Deploy to prod + Chrome smoke test (HARD-GATED on Task 10 success)

**Pre-condition:** Task 10 passed all 6 smoke-test steps. Do **not** start this task if Task 10 failed.

**Files:** none modified — this is a verification task.

- [ ] **Step 1: Populate the prod secret**

```bash
bash scripts/populate_mcp_keys.sh prod
```

Verify:

```bash
aws secretsmanager get-secret-value --secret-id holmesgpt-prod/mcp-api-keys \
  --profile pdi-platform-all --region us-east-1 --query SecretString --output text \
  | python -c "import json,sys; d=json.loads(sys.stdin.read()); print({k: 'SET' if v else 'EMPTY' for k,v in d.items()})"
```

Expected: all 4 keys show `SET`.

- [ ] **Step 2: Build and push prod image**

The prod ECR is in account `827852520868`. Build and push:

```bash
aws ecr get-login-password --region us-east-1 --profile pdi-platform-all \
  | docker login --username AWS --password-stdin 827852520868.dkr.ecr.us-east-1.amazonaws.com

docker build -f infra/Dockerfile.frontend \
  --build-arg VITE_OKTA_ISSUER="https://pdisoftware.okta.com/oauth2/default" \
  --build-arg VITE_OKTA_CLIENT_ID="0oa1ae04lowCIDE9B2p8" \
  -t 827852520868.dkr.ecr.us-east-1.amazonaws.com/holmesgpt:latest .

docker push 827852520868.dkr.ecr.us-east-1.amazonaws.com/holmesgpt:latest
```

Expected: push succeeds.

- [ ] **Step 3: Apply tofu for prod**

```bash
cd infra
PROFILE="pdi-platform-all"
REGION="us-east-1"

sm_get() {
  local raw=$(aws secretsmanager get-secret-value \
    --secret-id "holmesgpt-prod/$1" \
    --region "$REGION" --profile "$PROFILE" \
    --query SecretString --output text 2>/dev/null)
  if [ -z "$raw" ]; then echo ""; return; fi
  python3 -c "import json,sys; print(json.loads('''$raw''').get('$2',''))"
}

ANTHROPIC_API_KEY=$(sm_get "anthropic-api-key" "ANTHROPIC_API_KEY")
MCP_ADO=$(sm_get "mcp-api-keys" "MCP_ADO_API_KEY")
MCP_ATLASSIAN=$(sm_get "mcp-api-keys" "MCP_ATLASSIAN_API_KEY")
MCP_SALESFORCE=$(sm_get "mcp-api-keys" "MCP_SALESFORCE_API_KEY")
MCP_JENKINS=$(sm_get "mcp-api-keys" "MCP_JENKINS_API_KEY")

# Use the prod backend
~/.local/bin/tofu init -reconfigure -backend-config=envs/backend-prod.hcl

~/.local/bin/tofu apply -var-file=envs/prod.tfvars \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="mcp_ado_api_key=$MCP_ADO" \
  -var="mcp_atlassian_api_key=$MCP_ATLASSIAN" \
  -var="mcp_salesforce_api_key=$MCP_SALESFORCE" \
  -var="mcp_jenkins_api_key=$MCP_JENKINS" \
  -auto-approve
```

Expected: apply succeeds.

- [ ] **Step 4: Restart the prod deployment**

```bash
aws eks update-kubeconfig --name holmesgpt-prod --profile pdi-platform-all --region us-east-1
kubectl rollout restart deployment/holmes-holmes -n holmesgpt
kubectl rollout status deployment/holmes-holmes -n holmesgpt --timeout=180s
```

Expected: rollout successful.

- [ ] **Step 5: Health + env checks**

```bash
curl -s -o /dev/null -w "healthz:%{http_code}\n" https://holmesgpt.shared.platform.pditechnologies.com/healthz
curl -s -o /dev/null -w "readyz:%{http_code}\n" https://holmesgpt.shared.platform.pditechnologies.com/readyz
kubectl get secret -n holmesgpt holmes-holmes -o jsonpath='{.data.MCP_JENKINS_API_KEY}' | base64 -d | head -c 20
```

Expected: `200` / `200` / first 20 chars of a non-empty key.

- [ ] **Step 6: Chrome smoke test (prod)**

Open `https://holmesgpt.shared.platform.pditechnologies.com` in Chrome. Log in via Okta. Then:

1. **Verify legacy `atlassian-default` works** — Go to Instances page, find the `atlassian-default` instance. Click Test Connection. Expected: `{ok: true, status: success, tool_count: N > 0}`. (This instance has no secret_arn — it uses the env-var fallback that just became populated.)
2. **Confirm Jenkins in dropdown** — Check that `jenkins` appears in the "New Instance" type list.
3. **Chat smoke test** — Start a chat and ask: `"Find any open Jira incidents tagged for the checkout service"`. Expected: Holmes calls an Atlassian tool and returns results (or an empty list if no matches — either is fine, as long as the tool was invoked).

- [ ] **Step 7: Commit if anything changed**

```bash
git status
# If nothing changed, skip.
```

---

## Task 12: Final regression check

**Files:** none modified — verification only.

- [ ] **Step 1: Re-run the Python test suites affected by this work**

```bash
poetry run pytest tests/plugins/toolsets/test_pagerduty.py tests/frontend/ -v --no-cov
```

Expected counts:
- `test_pagerduty.py`: 28 passed (unchanged by this work).
- `test_instances_api.py`: 4 (existing PagerDuty) + 1 (new `TestJenkinsInMcpRegistry`) + 4 (new `TestMcpConnectionHelper`) = 9 tests passed.

Total: 37 passed.

- [ ] **Step 2: Frontend build sanity**

```bash
cd frontend && npm run build 2>&1 | tail -3
```

Expected: exit 0.

- [ ] **Step 3: Confirm both docs pages render in nav**

If time permits, run:

```bash
make docs-build 2>&1 | grep -Ei "atlassian-mcp|jenkins-mcp|warning|error" | head -20
```

Expected: no warnings mentioning either page.

If `make` is unavailable (Windows), skip — CI will validate.

- [ ] **Step 4: Final commit (if anything was left uncommitted)**

```bash
git status
# If clean, done. If not, commit the residual changes.
```

---

## Acceptance Criteria Mapping

| Spec criterion | Task |
|---|---|
| Atlassian MCP loads in prod | Task 10 (dev verify) + Task 11 (prod apply + smoke test) |
| Jenkins MCP is usable from Holmes | Tasks 2, 3, 4, 5, 6 (plumbing) + Task 10, 11 (deploy + verify) |
| Test Connection works for MCP instances | Task 7 (new `_test_mcp_instance_connection` + dispatcher) |
| Dev-then-prod hard gate | Task 10 → Task 11 ordering; Task 11 says "do not start if Task 10 failed" |
| Discoverable docs | Tasks 8, 9 (two docs pages + nav) |
| API keys never leaked in errors or logs | Task 7 unit test `test_atlassian_connection_strips_api_key_from_error` |
| Legacy `atlassian-default` instance keeps working | Task 11 Step 6.1 (Test Connection on it returns success via env-var fallback) |
