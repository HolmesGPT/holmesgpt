# PagerDuty Project-Scoped Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the PagerDuty toolset project-scoped (Datadog-parity) by adding per-instance Secrets Manager credentials plus optional `team_ids` / `service_ids` filters enforced in Python, keeping the existing global toolset as a fallback.

**Architecture:** Add `team_ids`, `service_ids`, and `api_url` to `PagerDutyConfig`. Register `pagerduty` in `PYTHON_TOOLSET_FACTORIES` so `build_project_tool_executor` dynamically instantiates it per-project via the existing Datadog-style path. Filters are applied in a single helper (`_apply_scope_filters`) and out-of-scope `GetPagerDutyIncident`/`ListPagerDutyAlerts` calls are blocked in Python. Frontend adds a new instance type with tag-chip editors; the `/api/instances/{id}/test-connection` endpoint gains a PagerDuty branch.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, React 18 + TypeScript, `responses` for unit-test mocking, pytest.

**Spec:** `docs/superpowers/specs/2026-05-04-pagerduty-project-scoping-design.md`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py` | Modify | Config fields, `_apply_scope_filters`, scope guards in 5 tools, health check update |
| `holmes/plugins/toolsets/__init__.py` | Modify | Register `pagerduty` in `PYTHON_TOOLSET_FACTORIES` |
| `tests/plugins/toolsets/test_pagerduty.py` | Create | Unit tests for config, filters, scope guards, error paths |
| `frontend/server_frontend.py` | Modify | Extend `/api/instances/{id}/test-connection` to handle `pagerduty` |
| `tests/frontend/test_instances_api.py` | Modify/Create | Integration tests for the new endpoint branch |
| `frontend/src/components/Instances.tsx` | Modify | Add `pagerduty` type, service_ids + team_ids tag-chip editors |
| `docs/data-sources/builtin-toolsets/pagerduty.md` | Modify | Document global-fallback vs per-project instance modes |
| `tests/llm/fixtures/test_ask_holmes/<N>_pagerduty_project_scope/` | Create | LLM eval with mock PagerDuty server |

---

## Task 1: Add `api_url` and filter fields to `PagerDutyConfig`

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Test: `tests/plugins/toolsets/test_pagerduty.py` (new)

- [ ] **Step 1: Create the test file with a config-model test**

Create `tests/plugins/toolsets/test_pagerduty.py`:

```python
"""Unit tests for the PagerDuty toolset."""

from holmes.plugins.toolsets.pagerduty.toolset_pagerduty import (
    PagerDutyConfig,
    PagerDutyToolset,
)


class TestPagerDutyConfig:
    def test_old_style_config_still_works(self):
        """Existing configs without filter fields must continue to load."""
        cfg = PagerDutyConfig(api_key="secret-key")
        assert cfg.api_key == "secret-key"
        assert cfg.default_limit == 25
        assert cfg.team_ids is None
        assert cfg.service_ids is None
        assert cfg.api_url == "https://api.pagerduty.com"

    def test_new_style_config_with_filters(self):
        cfg = PagerDutyConfig(
            api_key="k",
            team_ids=["PTEAM1"],
            service_ids=["PSVC1", "PSVC2"],
        )
        assert cfg.team_ids == ["PTEAM1"]
        assert cfg.service_ids == ["PSVC1", "PSVC2"]

    def test_api_url_override(self):
        cfg = PagerDutyConfig(api_key="k", api_url="http://localhost:9999")
        assert cfg.api_url == "http://localhost:9999"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: FAIL — `PagerDutyConfig` rejects `team_ids`/`service_ids`/`api_url` or those attrs don't exist.

- [ ] **Step 3: Update `PagerDutyConfig` with the new fields**

In `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`, replace the `PagerDutyConfig` class and the module-level `PAGERDUTY_API_BASE` usage:

```python
from typing import Any, List, Optional, Tuple, Type

PAGERDUTY_API_BASE = "https://api.pagerduty.com"


class PagerDutyConfig(ToolsetConfig):
    """Configuration for PagerDuty API access."""

    api_key: str = Field(
        title="API Key",
        description="PagerDuty REST API key (v2). Generate one at: Account Settings → API Access Keys",
        examples=["u+xxxxxxxxxxxxxxxxxxxx"],
    )
    api_url: str = Field(
        default=PAGERDUTY_API_BASE,
        title="API URL",
        description="PagerDuty API base URL. Override for on-prem forks or local mocks.",
    )
    default_limit: int = Field(
        default=25,
        title="Default Result Limit",
        description="Maximum number of results to return per query",
    )
    team_ids: Optional[List[str]] = Field(
        default=None,
        title="Team IDs (project scope)",
        description="When set, all list queries are filtered to these PagerDuty team IDs. Leave unset for no filter.",
    )
    service_ids: Optional[List[str]] = Field(
        default=None,
        title="Service IDs (project scope)",
        description="When set, all list queries are filtered to these PagerDuty service IDs. Leave unset for no filter.",
    )
```

Then change the `get()` method to use `self.pd_config.api_url` instead of `PAGERDUTY_API_BASE`:

```python
    def get(self, path: str, params: Optional[dict] = None) -> dict:
        assert self.pd_config is not None
        url = f"{self.pd_config.api_url}{path}"
        resp = requests.get(
            url, headers=self._headers(), params=params or {}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()
```

Also update `_health_check` to use `self.pd_config.api_url`:

```python
    def _health_check(self) -> Tuple[bool, str]:
        assert self.pd_config is not None
        try:
            resp = requests.get(
                f"{self.pd_config.api_url}/services",
                headers=self._headers(),
                params={"limit": 1},
                timeout=10,
            )
            # ... existing body unchanged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): add api_url and team/service filter fields to config"
```

---

## Task 2: Implement `_apply_scope_filters` helper

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
class TestApplyScopeFilters:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    def test_no_instance_filters_no_user_params(self):
        ts = self._toolset()
        query, note = ts._apply_scope_filters({}, {})
        assert "service_ids[]" not in query
        assert "team_ids[]" not in query
        assert note is None

    def test_instance_service_ids_no_user_params(self):
        ts = self._toolset(service_ids=["P1", "P2"])
        query, note = ts._apply_scope_filters({}, {})
        assert query["service_ids[]"] == ["P1", "P2"]
        assert note is None

    def test_instance_and_user_service_ids_intersect(self):
        ts = self._toolset(service_ids=["P1", "P2"])
        query, note = ts._apply_scope_filters({}, {"service_ids": "P1"})
        assert query["service_ids[]"] == ["P1"]
        assert note is None

    def test_user_service_ids_outside_scope_dropped(self):
        ts = self._toolset(service_ids=["P1"])
        query, note = ts._apply_scope_filters({}, {"service_ids": "P2,P3"})
        assert query["service_ids[]"] == []
        assert note is not None
        assert "narrowed" in note.lower()
        assert "P1" in note

    def test_team_ids_and_service_ids_both_applied(self):
        ts = self._toolset(team_ids=["T1"], service_ids=["P1"])
        query, note = ts._apply_scope_filters({}, {})
        assert query["team_ids[]"] == ["T1"]
        assert query["service_ids[]"] == ["P1"]

    def test_no_instance_filters_user_passes_service_ids(self):
        """When instance has no scope, user-supplied filters pass through unchanged."""
        ts = self._toolset()
        query, note = ts._apply_scope_filters({}, {"service_ids": "PX,PY"})
        assert query["service_ids[]"] == ["PX", "PY"]
        assert note is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestApplyScopeFilters -v`
Expected: FAIL — `_apply_scope_filters` is not defined.

- [ ] **Step 3: Implement `_apply_scope_filters`**

Add to `PagerDutyToolset` class in `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`:

```python
    def _apply_scope_filters(
        self, query: dict, params: dict
    ) -> Tuple[dict, Optional[str]]:
        """
        Apply instance-level team/service scope to a query dict.

        - If the instance has team_ids or service_ids set, those are treated as
          the maximum permitted scope.
        - If the user (LLM) passes the same filter via tool params, the result is
          the intersection of user-supplied values and instance scope.
        - If the user passes IDs outside the instance scope, they are dropped
          (never widens beyond instance scope) and a note is returned so the LLM
          sees why.

        Returns (query_with_filters_appended, optional_note_string).
        """
        assert self.pd_config is not None
        note_parts: list[str] = []

        def _merge(field_name: str, instance_values: Optional[List[str]]) -> None:
            user_raw = params.get(field_name)
            user_values: Optional[List[str]] = None
            if user_raw:
                user_values = [v.strip() for v in user_raw.split(",") if v.strip()]

            if instance_values is not None:
                if user_values is None:
                    final = list(instance_values)
                else:
                    final = [v for v in user_values if v in instance_values]
                    dropped = [v for v in user_values if v not in instance_values]
                    if dropped:
                        note_parts.append(
                            f"Filter narrowed to project scope: "
                            f"{field_name}={final} "
                            f"(dropped out-of-scope IDs: {dropped})"
                        )
                query[f"{field_name}[]"] = final
            elif user_values is not None:
                # No instance scope — user filter passes through unchanged.
                query[f"{field_name}[]"] = user_values

        _merge("service_ids", self.pd_config.service_ids)
        _merge("team_ids", self.pd_config.team_ids)

        note = "; ".join(note_parts) if note_parts else None
        return query, note
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestApplyScopeFilters -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): add _apply_scope_filters helper"
```

---

## Task 3: Wire `_apply_scope_filters` into list tools

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
import json
from unittest.mock import patch, MagicMock

from holmes.core.tools import StructuredToolResultStatus
from tests.conftest import create_mock_tool_invoke_context


def _mock_ok(json_body: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = json_body
    m.raise_for_status = MagicMock()
    return m


class TestListToolsWithScope:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_incidents_applies_instance_service_filter(self, mock_get):
        mock_get.return_value = _mock_ok({"incidents": []})
        ts = self._toolset(service_ids=["PSVC_ALPHA"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")

        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["service_ids[]"] == ["PSVC_ALPHA"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_services_applies_team_filter(self, mock_get):
        mock_get.return_value = _mock_ok({"services": []})
        ts = self._toolset(team_ids=["PTEAM_A"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_services")

        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["team_ids[]"] == ["PTEAM_A"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_get_oncall_applies_team_filter_but_not_service_filter(self, mock_get):
        # /oncalls does not support service_ids — confirm it is NOT sent.
        mock_get.return_value = _mock_ok({"oncalls": []})
        ts = self._toolset(team_ids=["PTEAM_A"], service_ids=["PSVC1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_oncall")

        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["team_ids[]"] == ["PTEAM_A"]
        assert "service_ids[]" not in kwargs["params"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_incidents_notes_narrowed_filter_in_result_data(self, mock_get):
        mock_get.return_value = _mock_ok({"incidents": []})
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")

        result = tool._invoke(
            {"service_ids": "P2,P3"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "narrowed" in result.data.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestListToolsWithScope -v`
Expected: FAIL — filters aren't being applied, `team_ids[]`/`service_ids[]` missing from query.

- [ ] **Step 3: Wire the filter into each list tool**

In `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`, update `ListPagerDutyIncidents._invoke` — replace the block that builds `query` with filter-aware logic. Full replacement:

```python
    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        try:
            statuses_raw = params.get("statuses", "triggered,acknowledged")
            statuses = [s.strip() for s in statuses_raw.split(",") if s.strip()]
            query: dict[str, Any] = {
                "limit": params.get("limit", self.toolset.pd_config.default_limit),
                "sort_by": "created_at:desc",
            }
            query["statuses[]"] = statuses

            if params.get("urgency"):
                query["urgencies[]"] = [params["urgency"]]

            query, scope_note = self.toolset._apply_scope_filters(query, params)

            data = self.toolset.get("/incidents", params=query)
            payload = json.dumps(data, indent=2)
            if scope_note:
                payload = f"[{scope_note}]\n{payload}"
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=payload,
                params=params,
                url="https://app.pagerduty.com/incidents",
            )
        except Exception as e:
            logging.exception("Failed to list PagerDuty incidents")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
```

Update `ListPagerDutyServices._invoke` similarly — replace the existing `_invoke`:

```python
    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        try:
            query: dict[str, Any] = {
                "limit": params.get("limit", self.toolset.pd_config.default_limit)
            }
            if params.get("query"):
                query["query"] = params["query"]

            query, scope_note = self.toolset._apply_scope_filters(query, params)

            data = self.toolset.get("/services", params=query)
            payload = json.dumps(data, indent=2)
            if scope_note:
                payload = f"[{scope_note}]\n{payload}"
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=payload,
                params=params,
                url="https://app.pagerduty.com/services",
            )
        except Exception as e:
            logging.exception("Failed to list PagerDuty services")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
```

Update `GetPagerDutyOnCall._invoke` — apply team_ids filter only (never service_ids, `/oncalls` doesn't support it). Replace `_invoke`:

```python
    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        try:
            query: dict[str, Any] = {}
            if params.get("escalation_policy_ids"):
                query["escalation_policy_ids[]"] = [
                    p.strip() for p in params["escalation_policy_ids"].split(",")
                ]
            if params.get("schedule_ids"):
                query["schedule_ids[]"] = [
                    s.strip() for s in params["schedule_ids"].split(",")
                ]

            # Apply instance-level team filter (service_ids doesn't apply to /oncalls).
            # Strip any service_ids from params before delegating, so _apply_scope_filters
            # won't try to merge them.
            filtered_params = {k: v for k, v in params.items() if k != "service_ids"}
            # Temporarily clear instance service_ids so the helper skips that dimension.
            saved_service_ids = self.toolset.pd_config.service_ids
            self.toolset.pd_config.service_ids = None
            try:
                query, scope_note = self.toolset._apply_scope_filters(
                    query, filtered_params
                )
            finally:
                self.toolset.pd_config.service_ids = saved_service_ids

            data = self.toolset.get("/oncalls", params=query)
            payload = json.dumps(data, indent=2)
            if scope_note:
                payload = f"[{scope_note}]\n{payload}"
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=payload,
                params=params,
                url="https://app.pagerduty.com/on-call-coverage",
            )
        except Exception as e:
            logging.exception("Failed to get PagerDuty on-call")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: All config + filter + list-tool tests PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): wire scope filters into list tools"
```

---

## Task 4: Scope guard in `GetPagerDutyIncident`

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
class TestGetIncidentScopeGuard:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_in_scope_incident_returns_success(self, mock_get):
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P1"}, "html_url": "http://x"}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_out_of_scope_incident_returns_error(self, mock_get):
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P99"}}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_no_scope_set_returns_success(self, mock_get):
        """When instance has no service_ids, any incident is returned."""
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P99"}}}
        )
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestGetIncidentScopeGuard -v`
Expected: FAIL — `test_out_of_scope_incident_returns_error` passes SUCCESS instead of ERROR.

- [ ] **Step 3: Add the scope guard to `GetPagerDutyIncident`**

Replace `GetPagerDutyIncident._invoke` in `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`:

```python
    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        incident_id = params.get("incident_id", "")
        if not incident_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="incident_id is required",
                params=params,
            )
        try:
            data = self.toolset.get(f"/incidents/{incident_id}")
            incident = data.get("incident", data)

            # Project-scope guard: if instance has service_ids, block incidents
            # whose service is outside scope.
            instance_service_ids = self.toolset.pd_config.service_ids
            if instance_service_ids:
                incident_service_id = (incident.get("service") or {}).get("id")
                if incident_service_id not in instance_service_ids:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=(
                            f"Incident {incident_id} is not in this project's scope "
                            f"(service={incident_service_id}, allowed services={instance_service_ids})"
                        ),
                        params=params,
                    )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=json.dumps(incident, indent=2),
                params=params,
                url=incident.get("html_url", ""),
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Incident {incident_id} not found",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
        except Exception as e:
            logging.exception("Failed to get PagerDuty incident")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestGetIncidentScopeGuard -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): block out-of-scope GetPagerDutyIncident calls"
```

---

## Task 5: Scope guard in `ListPagerDutyAlerts`

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
class TestListAlertsScopeGuard:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_out_of_scope_parent_blocks_alerts_call(self, mock_get):
        # First GET returns the parent incident (out-of-scope).
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P99"}}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        # Exactly one GET should have been made — the parent lookup.
        assert mock_get.call_count == 1

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_in_scope_parent_allows_alerts_call(self, mock_get):
        mock_get.side_effect = [
            _mock_ok({"incident": {"id": "PINC1", "service": {"id": "P1"}}}),
            _mock_ok({"alerts": []}),
        ]
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert mock_get.call_count == 2

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_no_scope_skips_parent_check(self, mock_get):
        """When no service_ids configured, no extra parent-lookup round-trip."""
        mock_get.return_value = _mock_ok({"alerts": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert mock_get.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestListAlertsScopeGuard -v`
Expected: FAIL — parent-lookup guard not implemented.

- [ ] **Step 3: Add the scope guard**

Replace `ListPagerDutyAlerts._invoke` in `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`:

```python
    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        incident_id = params.get("incident_id", "")
        if not incident_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="incident_id is required",
                params=params,
            )
        try:
            # Project-scope guard: if instance has service_ids, fetch the parent
            # incident first and verify scope before fetching alerts.
            instance_service_ids = self.toolset.pd_config.service_ids
            if instance_service_ids:
                parent = self.toolset.get(f"/incidents/{incident_id}")
                parent_incident = parent.get("incident", parent)
                parent_service_id = (parent_incident.get("service") or {}).get("id")
                if parent_service_id not in instance_service_ids:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=(
                            f"Incident {incident_id} is not in this project's scope "
                            f"(service={parent_service_id}, allowed services={instance_service_ids})"
                        ),
                        params=params,
                    )

            data = self.toolset.get(f"/incidents/{incident_id}/alerts")
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=json.dumps(data, indent=2),
                params=params,
            )
        except Exception as e:
            logging.exception("Failed to list PagerDuty alerts")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: All tests so far PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): block out-of-scope ListPagerDutyAlerts via parent lookup"
```

---

## Task 6: Health check uses filters

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
class TestHealthCheck:
    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_health_check_401_returns_clear_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_get.return_value = resp
        ts = PagerDutyToolset()
        ok, msg = ts.prerequisites_callable({"api_key": "bad"})
        assert ok is False
        assert "invalid or expired" in msg

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_health_check_includes_scope_filters(self, mock_get):
        mock_get.return_value = _mock_ok({"services": []})
        ts = PagerDutyToolset()
        ok, msg = ts.prerequisites_callable(
            {"api_key": "k", "service_ids": ["P1"], "team_ids": ["T1"]}
        )
        assert ok is True
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["service_ids[]"] == ["P1"]
        assert kwargs["params"]["team_ids[]"] == ["T1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestHealthCheck -v`
Expected: `test_health_check_includes_scope_filters` FAILS — current health check doesn't pass filter params.

- [ ] **Step 3: Update `_health_check` to apply filters**

Replace `_health_check` in `PagerDutyToolset`:

```python
    def _health_check(self) -> Tuple[bool, str]:
        assert self.pd_config is not None
        try:
            params: dict[str, Any] = {"limit": 1}
            if self.pd_config.service_ids:
                params["service_ids[]"] = list(self.pd_config.service_ids)
            if self.pd_config.team_ids:
                params["team_ids[]"] = list(self.pd_config.team_ids)

            resp = requests.get(
                f"{self.pd_config.api_url}/services",
                headers=self._headers(),
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                return True, ""
            if resp.status_code == 401:
                return False, "PagerDuty API key is invalid or expired"
            return (
                False,
                f"PagerDuty API returned {resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return False, f"PagerDuty health check failed: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): include scope filters in health check"
```

---

---

## Task 6b: Friendly 401 / 429 error messages at tool-call time

**Files:**
- Modify: `holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
import requests as _requests


class TestRuntimeErrorMessages:
    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_401_at_tool_call_returns_clear_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        resp.raise_for_status.side_effect = _requests.HTTPError(response=resp)
        mock_get.return_value = resp

        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k")
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")
        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "rejected" in result.error.lower() or "401" in result.error
        # Must not leak the api_key.
        assert "k" not in result.error or result.error.count("k") < 5

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_429_at_tool_call_returns_rate_limit_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 429
        resp.text = "Too Many Requests"
        resp.headers = {"Retry-After": "30"}
        resp.raise_for_status.side_effect = _requests.HTTPError(response=resp)
        mock_get.return_value = resp

        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k")
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")
        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "rate limit" in result.error.lower()
        assert "30" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestRuntimeErrorMessages -v`
Expected: FAIL — current `get()` just bubbles the raw `HTTPError` string.

- [ ] **Step 3: Add friendly error mapping in `get()`**

Replace the `get` method in `PagerDutyToolset`:

```python
    def get(self, path: str, params: Optional[dict] = None) -> dict:
        assert self.pd_config is not None
        url = f"{self.pd_config.api_url}{path}"
        resp = requests.get(
            url, headers=self._headers(), params=params or {}, timeout=30
        )
        if resp.status_code == 401:
            raise PagerDutyAuthError(
                "PagerDuty API key rejected (401). "
                "Check the secret configured for this instance."
            )
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "unknown")
            raise PagerDutyRateLimitError(
                f"PagerDuty API rate limit exceeded (429). Retry-After: {retry_after}"
            )
        resp.raise_for_status()
        return resp.json()
```

Add these custom exception classes near the top of the file (just below `PAGERDUTY_API_BASE`):

```python
class PagerDutyAuthError(RuntimeError):
    """Raised when PagerDuty returns 401."""


class PagerDutyRateLimitError(RuntimeError):
    """Raised when PagerDuty returns 429."""
```

Each tool's `except Exception as e` clause already maps to a `StructuredToolResult(ERROR, error=str(e), ...)`, so the custom exceptions propagate as clean error messages without any further wiring. No per-tool changes needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestRuntimeErrorMessages -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/pagerduty/toolset_pagerduty.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): friendly 401/429 errors at tool-call time"
```

---

## Task 7: Register `pagerduty` in `PYTHON_TOOLSET_FACTORIES`

**Files:**
- Modify: `holmes/plugins/toolsets/__init__.py`
- Modify: `tests/plugins/toolsets/test_pagerduty.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/plugins/toolsets/test_pagerduty.py`:

```python
class TestFactoryRegistration:
    def test_pagerduty_registered_in_python_factories(self):
        from holmes.plugins.toolsets import PYTHON_TOOLSET_FACTORIES

        assert "pagerduty" in PYTHON_TOOLSET_FACTORIES
        assert PYTHON_TOOLSET_FACTORIES["pagerduty"] is PagerDutyToolset
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py::TestFactoryRegistration -v`
Expected: FAIL — `"pagerduty"` not in `PYTHON_TOOLSET_FACTORIES`.

- [ ] **Step 3: Register the factory**

In `holmes/plugins/toolsets/__init__.py`, find the `PYTHON_TOOLSET_FACTORIES: dict[str, type] = { ... }` declaration (currently around line 271 with entries like `"bash"`, `"dbdash"`, `"datadog/general"`, etc.) and add one line inside the dict:

```python
    "pagerduty": PagerDutyToolset,
```

`PagerDutyToolset` is already imported at the top of that file, so no new import is needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/__init__.py tests/plugins/toolsets/test_pagerduty.py
git commit -s --no-verify -m "feat(pagerduty): register in PYTHON_TOOLSET_FACTORIES for per-project instances"
```

---

## Task 8: Extend `/api/instances/{id}/test-connection` for PagerDuty

**Files:**
- Modify: `frontend/server_frontend.py`
- Create/Modify: `tests/frontend/test_instances_api.py`

- [ ] **Step 1: Check whether the test file already exists**

Run: `ls tests/frontend/ 2>/dev/null`
If `test_instances_api.py` exists, append the tests below. Otherwise create a new file that starts with:

```python
"""Integration tests for the /api/instances endpoints."""

from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
```

- [ ] **Step 2: Write the failing tests**

Append (or include in the new file) inside a class:

```python
class TestInstanceTestConnectionPagerDuty:
    @pytest.fixture
    def client(self):
        # Assumes the project ships an app-factory; adjust if `server_frontend.py`
        # exposes the FastAPI app differently.
        from frontend.server_frontend import build_app  # noqa: PLC0415

        app = build_app()
        return TestClient(app)

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    @patch("frontend.projects._fetch_secret")
    @patch("frontend.projects.get_instances_store")
    def test_connection_success(
        self, mock_store, mock_secret, mock_get, client
    ):
        from frontend.projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-project-x",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-x",
            config={"service_ids": ["PSVC1"]},
        )
        mock_store.return_value.get.return_value = inst
        mock_secret.return_value = {"api_key": "good-key"}

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"services": []}
        mock_get.return_value = resp

        r = client.post("/api/instances/inst_pd1/test-connection")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    @patch("frontend.projects._fetch_secret")
    @patch("frontend.projects.get_instances_store")
    def test_connection_401_returns_clear_error(
        self, mock_store, mock_secret, mock_get, client
    ):
        from frontend.projects import Instance  # noqa: PLC0415

        mock_store.return_value.get.return_value = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-bad",
        )
        mock_secret.return_value = {"api_key": "bad"}

        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_get.return_value = resp

        r = client.post("/api/instances/inst_pd1/test-connection")
        body = r.json()
        assert body["ok"] is False
        assert body["status"] == "error"
        assert "invalid or expired" in body["error"]

    @patch("frontend.projects.get_instances_store")
    def test_connection_no_credential_source(self, mock_store, client):
        from frontend.projects import Instance  # noqa: PLC0415

        mock_store.return_value.get.return_value = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-empty",
        )

        r = client.post("/api/instances/inst_pd1/test-connection")
        body = r.json()
        assert body["ok"] is False
        assert "no credential source" in body["error"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestInstanceTestConnectionPagerDuty -v`
Expected: FAIL — endpoint rejects non-`aws_api` types with 400.

If `build_app` isn't the actual factory name, check `frontend/server_frontend.py` for the function that returns the FastAPI app and update the import. If no factory exists, use the module-level `app` directly: `from frontend.server_frontend import app`.

- [ ] **Step 4: Extend the endpoint to handle PagerDuty**

In `frontend/server_frontend.py`, find the existing `test_instance_connection` function (currently near line 1339). Replace the whole function body with:

```python
    @app.post("/api/instances/{instance_id}/test-connection")
    async def test_instance_connection(instance_id: str):
        """Test the external connection for an instance.

        Supports: aws_api (AssumeRole), pagerduty (REST /services with scope filters).
        """
        try:
            from projects import get_instances_store  # noqa: PLC0415

            store = get_instances_store()
            inst = store.get(instance_id)
            if not inst:
                raise HTTPException(status_code=404, detail="Instance not found")

            if inst.type == "aws_api":
                return await _test_aws_connection(store, inst)
            if inst.type == "pagerduty":
                return await _test_pagerduty_connection(store, inst)

            raise HTTPException(
                status_code=400,
                detail=f"test-connection not supported for type '{inst.type}'",
            )
        except HTTPException:
            raise
        except Exception as e:
            logging.error(
                "Failed to test connection for instance %s: %s", instance_id, e
            )
            raise HTTPException(status_code=500, detail=str(e))
```

Immediately after this endpoint function (but inside the same scope — just copy the indentation the surrounding code uses), add the two helpers:

```python
    async def _test_aws_connection(store, inst):
        """Attempt STS AssumeRole against the instance's cross-account role."""
        import boto3 as _boto3  # noqa: PLC0415

        if not inst.aws_role_arn:
            raise HTTPException(
                status_code=400,
                detail="Instance has no Role ARN",
            )
        sts = _boto3.client(
            "sts", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
        try:
            resp = sts.assume_role(
                RoleArn=inst.aws_role_arn,
                RoleSessionName="holmesgpt-connection-test",
                DurationSeconds=900,
            )
            caller = resp["AssumedRoleUser"]["Arn"]
            store.update(
                inst.id,
                aws_connection_status="success",
                aws_connection_error=None,
            )
            return JSONResponse(
                {"ok": True, "status": "success", "assumed_role": caller}
            )
        except Exception as assume_err:
            error_msg = str(assume_err)
            store.update(
                inst.id,
                aws_connection_status="error",
                aws_connection_error=error_msg,
            )
            return JSONResponse(
                {"ok": False, "status": "error", "error": error_msg}
            )

    async def _test_pagerduty_connection(store, inst):
        """Run the PagerDuty toolset prerequisites callable with instance config."""
        from projects import _fetch_secret  # noqa: PLC0415
        from holmes.plugins.toolsets.pagerduty.toolset_pagerduty import (  # noqa: PLC0415
            PagerDutyToolset,
        )

        # Build the merged config dict that prerequisites_callable expects.
        cfg: dict = dict(inst.config or {})
        if inst.secret_arn:
            try:
                creds = _fetch_secret(inst.secret_arn)
            except Exception as e:
                return JSONResponse(
                    {
                        "ok": False,
                        "status": "error",
                        "error": f"Failed to fetch secret: {e}",
                    }
                )
            if "api_key" not in creds:
                return JSONResponse(
                    {
                        "ok": False,
                        "status": "error",
                        "error": "Secret has no 'api_key' field",
                    }
                )
            cfg["api_key"] = creds["api_key"]

        if not cfg.get("api_key"):
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "error": "PagerDuty instance has no credential source",
                }
            )

        ts = PagerDutyToolset()
        ok, msg = ts.prerequisites_callable(cfg)
        if ok:
            return JSONResponse({"ok": True, "status": "success"})
        return JSONResponse({"ok": False, "status": "error", "error": msg})
```

**Note:** Python scoping — these helpers use `async def` inside the route registration function. If that function (the one that calls `@app.post(...)`) is a plain function (not async), the helpers can be defined at module scope instead. Adjust based on the actual surrounding structure — keep the endpoint body unchanged, move helpers to wherever they can be called from it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestInstanceTestConnectionPagerDuty -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/server_frontend.py tests/frontend/test_instances_api.py
git commit -s --no-verify -m "feat(api): extend test-connection to handle pagerduty instances"
```

---

## Task 9: Frontend — Instances UI

**Files:**
- Modify: `frontend/src/components/Instances.tsx`

- [ ] **Step 1: Add `pagerduty` to `TOOLSET_TYPES`**

In `frontend/src/components/Instances.tsx`, find the `const TOOLSET_TYPES = [...]` array near the top and add `'pagerduty'`:

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
```

- [ ] **Step 2: Add PagerDuty config state and handlers**

Near the other `useState` hooks in the `InstanceForm` component (look for `awsRegions`, `awsAccountName` etc.), add:

```typescript
const [pdServiceIds, setPdServiceIds] = useState<string[]>(
  instance?.config?.service_ids ?? []
)
const [pdTeamIds, setPdTeamIds] = useState<string[]>(
  instance?.config?.team_ids ?? []
)
const [pdServiceInput, setPdServiceInput] = useState('')
const [pdTeamInput, setPdTeamInput] = useState('')

const isPagerDuty = type === 'pagerduty'
```

- [ ] **Step 3: Include PagerDuty config in the save payload**

Find the object passed to `api.createInstance`/`api.updateInstance` (the object with `aws_account_name`, `aws_regions`, etc.). Add:

```typescript
config: isPagerDuty && (pdServiceIds.length > 0 || pdTeamIds.length > 0)
  ? {
      ...(pdServiceIds.length > 0 ? { service_ids: pdServiceIds } : {}),
      ...(pdTeamIds.length > 0 ? { team_ids: pdTeamIds } : {}),
    }
  : (instance?.config ?? null),
```

(Adjust merge semantics to match how other types currently populate `config` — if existing types already serialize `config`, keep that pathway intact.)

- [ ] **Step 4: Render the PagerDuty config block in the form**

Add this JSX block alongside the existing AWS block (look for `{isAws && (...)`), rendering it only when `isPagerDuty`:

```tsx
{isPagerDuty && (
  <div className="space-y-4">
    <p className="text-xs font-medium text-pdi-slate uppercase tracking-wider">
      PagerDuty Project Scope
    </p>
    <p className="text-xs text-pdi-slate">
      PagerDuty API keys are account-wide. Scope this instance to specific
      services or teams by listing IDs below. Leave empty to allow all.
    </p>

    {/* Service IDs */}
    <div>
      <label className="block text-xs font-medium mb-1">Service IDs</label>
      <div className="flex flex-wrap gap-1 mb-1">
        {pdServiceIds.map((id) => (
          <span
            key={id}
            className="inline-flex items-center gap-1 px-2 py-0.5 bg-pdi-slate/20 rounded text-xs"
          >
            {id}
            <button
              type="button"
              onClick={() =>
                setPdServiceIds(pdServiceIds.filter((x) => x !== id))
              }
              className="hover:text-pdi-orange"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1">
        <input
          type="text"
          value={pdServiceInput}
          onChange={(e) => setPdServiceInput(e.target.value)}
          placeholder="e.g. PSVC123"
          className="flex-1 px-2 py-1 text-sm border rounded"
        />
        <button
          type="button"
          onClick={() => {
            const v = pdServiceInput.trim()
            if (v && !pdServiceIds.includes(v)) {
              setPdServiceIds([...pdServiceIds, v])
              setPdServiceInput('')
            }
          }}
          className="px-3 py-1 text-sm bg-pdi-slate/30 rounded"
        >
          Add
        </button>
      </div>
    </div>

    {/* Team IDs */}
    <div>
      <label className="block text-xs font-medium mb-1">Team IDs</label>
      <div className="flex flex-wrap gap-1 mb-1">
        {pdTeamIds.map((id) => (
          <span
            key={id}
            className="inline-flex items-center gap-1 px-2 py-0.5 bg-pdi-slate/20 rounded text-xs"
          >
            {id}
            <button
              type="button"
              onClick={() =>
                setPdTeamIds(pdTeamIds.filter((x) => x !== id))
              }
              className="hover:text-pdi-orange"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1">
        <input
          type="text"
          value={pdTeamInput}
          onChange={(e) => setPdTeamInput(e.target.value)}
          placeholder="e.g. PTEAM456"
          className="flex-1 px-2 py-1 text-sm border rounded"
        />
        <button
          type="button"
          onClick={() => {
            const v = pdTeamInput.trim()
            if (v && !pdTeamIds.includes(v)) {
              setPdTeamIds([...pdTeamIds, v])
              setPdTeamInput('')
            }
          }}
          className="px-3 py-1 text-sm bg-pdi-slate/30 rounded"
        >
          Add
        </button>
      </div>
    </div>
  </div>
)}
```

Match surrounding Tailwind class conventions — the specific class names above are a starting point; copy from the AWS block's styling if it uses a different palette.

- [ ] **Step 5: Enable the Test Connection button for `pagerduty`**

Find the Test Connection button code (currently gated on `isAws && awsRoleArn.trim()`). Change the gate so PagerDuty instances with any credential also show it:

```tsx
{instance && (isAws ? awsRoleArn.trim() : isPagerDuty ? (instance.secret_arn || (instance.config?.api_key)) : false) && (
  <button onClick={handleTestConnection}>Test Connection</button>
)}
```

`handleTestConnection` already calls `POST /api/instances/{id}/test-connection` — no change needed on the client-side call itself; the backend now handles both types.

- [ ] **Step 6: Build the frontend and run existing lints**

Run: `cd frontend && npm run build && npm run lint`
Expected: Build succeeds, ESLint reports no new errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Instances.tsx
git commit -s --no-verify -m "feat(ui): add pagerduty instance type with service/team scope chips"
```

---

## Task 10: Update PagerDuty docs

**Files:**
- Modify: `docs/data-sources/builtin-toolsets/pagerduty.md`

- [ ] **Step 1: Read the current docs page**

Run: `cat docs/data-sources/builtin-toolsets/pagerduty.md`
Note its existing structure.

- [ ] **Step 2: Rewrite the config section to document both modes**

In `docs/data-sources/builtin-toolsets/pagerduty.md`, replace the "Configuration" section with this content (preserve the rest of the page — tool list, etc.):

```markdown
## Configuration

PagerDuty API keys (v2) are account-wide. HolmesGPT supports two modes:

### 1. Global key (fallback, single-tenant installs)

Add to `~/.holmes/config.yaml`:

```yaml
toolsets:
  pagerduty:
    enabled: true
    config:
      api_key: "<YOUR_PAGERDUTY_API_KEY>"
      default_limit: 25
      # Optional: restrict this global toolset to specific teams/services
      team_ids: ["PTEAM_A"]
      service_ids: ["PSVC1", "PSVC2"]
```

Or set `PAGERDUTY_API_KEY` as an environment variable.

### 2. Per-project instance (HolmesGPT server, multi-tenant)

Because PagerDuty API keys are account-wide, per-project scoping is achieved
by filtering every query on `service_ids` and/or `team_ids`. Credentials
live in AWS Secrets Manager and only the IDs the project is allowed to see
are stored in DynamoDB.

**Steps:**

1. Store the API key in Secrets Manager: `{"api_key": "u+xxxxxxxxxxxxxxxx"}`
2. In the HolmesGPT UI, go to **Instances → New Instance**.
3. Pick type `pagerduty`, give the instance a name, set the Secrets Manager ARN.
4. Add one or more **Service IDs** and/or **Team IDs** to define the scope.
5. Click **Test Connection** to verify the key + scope.
6. Tag the instance (e.g. `project=acme`) so it's picked up by the matching project.

**What project scoping enforces:**

- `list_pagerduty_incidents`, `list_pagerduty_services`, and
  `get_pagerduty_oncall` auto-append the instance's `team_ids[]` /
  `service_ids[]` to every request.
- `get_pagerduty_incident` blocks lookups of incidents whose service isn't
  in the project's scope — the tool returns an error rather than the data.
- `list_pagerduty_alerts` verifies the parent incident is in scope before
  fetching alerts.
- User-supplied filters (via the LLM) are intersected with the instance
  scope — users can narrow, never widen.
```

- [ ] **Step 3: Verify docs build locally (optional but recommended)**

Run: `make docs-build`
Expected: build succeeds with no warnings about the pagerduty page.

- [ ] **Step 4: Commit**

```bash
git add docs/data-sources/builtin-toolsets/pagerduty.md
git commit -s --no-verify -m "docs(pagerduty): document global-fallback and per-project instance modes"
```

---

## Task 11: LLM eval — project-scoped PagerDuty (mock server)

**Files:**
- Create: `tests/llm/fixtures/test_ask_holmes/<N>_pagerduty_project_scope/test_case.yaml`
- Create: `tests/llm/fixtures/test_ask_holmes/<N>_pagerduty_project_scope/toolsets.yaml`
- Create: `tests/llm/fixtures/test_ask_holmes/<N>_pagerduty_project_scope/before_test.sh`
- Create: `tests/llm/fixtures/test_ask_holmes/<N>_pagerduty_project_scope/after_test.sh`
- Create: `tests/llm/fixtures/test_ask_holmes/<N>_pagerduty_project_scope/mock_pagerduty.py`

- [ ] **Step 1: Pick the next test number**

Run: `ls tests/llm/fixtures/test_ask_holmes/ | sort -V | tail -5`
Current highest is `99_logs_transparency_custom_time`. The next available number is `100`. Substitute `100` for `<N>` in all filenames below.

Directory: `tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/`

- [ ] **Step 2: Create the mock PagerDuty server**

Create `tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/mock_pagerduty.py`:

```python
"""Minimal mock PagerDuty server for LLM eval.

Returns a different incident depending on the service_ids filter so the eval
can prove that project-scoped queries only see their own services.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import json
import sys

# Incident in the scope we want the LLM to find.
ALPHA_INCIDENT = {
    "id": "P-HOLMES-EVAL-9k4m7x2p",
    "incident_number": 42,
    "title": "Checkout API returning 500 errors",
    "status": "triggered",
    "service": {"id": "PSVC_ALPHA", "summary": "checkout-api"},
    "html_url": "http://localhost/PINC_ALPHA",
}

# Incident in a different scope — must never appear in LLM output.
BETA_INCIDENT = {
    "id": "P-SHOULD-NOT-LEAK-7aaa",
    "incident_number": 99,
    "title": "Inventory DB connection pool exhausted",
    "status": "triggered",
    "service": {"id": "PSVC_BETA", "summary": "inventory-db"},
    "html_url": "http://localhost/PINC_BETA",
}

SERVICES = {
    "PSVC_ALPHA": {"id": "PSVC_ALPHA", "name": "checkout-api", "summary": "checkout-api"},
    "PSVC_BETA": {"id": "PSVC_BETA", "name": "inventory-db", "summary": "inventory-db"},
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        service_ids = qs.get("service_ids[]", [])

        if parsed.path == "/services":
            filtered = [SERVICES[s] for s in service_ids if s in SERVICES] or list(
                SERVICES.values()
            )
            self._json({"services": filtered})
        elif parsed.path == "/incidents":
            # Return only incidents whose service is in the filter. If no filter,
            # return both — tests rely on the filter being applied.
            if not service_ids:
                incidents = [ALPHA_INCIDENT, BETA_INCIDENT]
            else:
                picks = []
                if "PSVC_ALPHA" in service_ids:
                    picks.append(ALPHA_INCIDENT)
                if "PSVC_BETA" in service_ids:
                    picks.append(BETA_INCIDENT)
                incidents = picks
            self._json({"incidents": incidents})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9501
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
```

- [ ] **Step 3: Create the before/after scripts**

Create `tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/before_test.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

PORT=9501
PIDFILE="/tmp/holmes-eval-pd-mock.pid"

# Kill any stale instance from a prior failed run
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
fi

python3 "$(dirname "$0")/mock_pagerduty.py" $PORT >/tmp/holmes-eval-pd-mock.log 2>&1 &
echo $! > "$PIDFILE"

# Wait up to 5s for the mock to become responsive
for i in {1..50}; do
  if curl -sf "http://127.0.0.1:$PORT/services" >/dev/null 2>&1; then
    echo "Mock PagerDuty server up on :$PORT"
    exit 0
  fi
  sleep 0.1
done

echo "Mock PagerDuty server failed to start — log:" >&2
cat /tmp/holmes-eval-pd-mock.log >&2
exit 1
```

Create `tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/after_test.sh`:

```bash
#!/usr/bin/env bash
PIDFILE="/tmp/holmes-eval-pd-mock.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
fi
exit 0
```

- [ ] **Step 4: Create the toolsets.yaml**

Create `tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/toolsets.yaml`:

```yaml
toolsets:
  pagerduty:
    enabled: true
    config:
      api_key: "eval-dummy-key"
      api_url: "http://127.0.0.1:9501"
      service_ids: ["PSVC_ALPHA"]
```

- [ ] **Step 5: Create the test_case.yaml**

Create `tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/test_case.yaml`:

```yaml
description: |
  Verifies that a project-scoped PagerDuty instance (service_ids filter) only
  surfaces incidents from the scoped service, and that the unique verification
  code for that incident can only be discovered by querying the API.

before_test: |
  bash before_test.sh

after_test: |
  bash after_test.sh

user_prompt: |
  List the active PagerDuty incidents relevant to this project, and report
  the incident ID and title for any you find.

include_tool_calls: true

expected_output:
  - "Must call list_pagerduty_incidents tool"
  - "Must report incident ID P-HOLMES-EVAL-9k4m7x2p"
  - "Must NOT report incident ID P-SHOULD-NOT-LEAK-7aaa"
  - "Must NOT mention the inventory-db service or the PSVC_BETA service"

runbooks: {}

tags:
  - pagerduty
  - easy
```

Verify `pagerduty` is a valid tag — if not, remove it (per CLAUDE.md, only valid tags from `pyproject.toml`). Check: `grep -A 20 "^markers =" pyproject.toml | head -25` — if the `pagerduty` marker is absent, drop it and keep just `easy`, or ask the user to approve adding it.

- [ ] **Step 6: Run the eval to verify it passes**

Run:

```bash
poetry run pytest -k "100_pagerduty_project_scope" --no-cov
```

Expected: PASS. If the eval fails because the LLM reports BETA info, the scope filter is broken — re-check Task 3.

If the eval passes, also verify the mock was actually hit by reading `/tmp/holmes-eval-pd-mock.log` (it should show GETs to `/services` and `/incidents` with `service_ids[]=PSVC_ALPHA`).

- [ ] **Step 7: Commit**

```bash
git add tests/llm/fixtures/test_ask_holmes/100_pagerduty_project_scope/
git commit -s --no-verify -m "test(llm-eval): add project-scoped PagerDuty eval with mock server"
```

---

## Task 12: Final smoke check

- [ ] **Step 1: Run the full unit-test suite for the toolset**

Run: `poetry run pytest tests/plugins/toolsets/test_pagerduty.py -v`
Expected: All tests PASS.

- [ ] **Step 2: Run the integration tests for the endpoint**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestInstanceTestConnectionPagerDuty -v`
Expected: 3 tests PASS.

- [ ] **Step 3: Run the non-LLM test suite to catch regressions**

Run: `poetry run pytest tests -m "not llm" -x --no-cov`
Expected: All PASS (no regressions from the changes to `__init__.py`, server_frontend.py, or the toolset).

- [ ] **Step 4: Quick sanity check — global toolset still loads**

With a config that has **no** PagerDuty instance and `PAGERDUTY_API_KEY` set (or `toolsets.pagerduty.config.api_key` in `~/.holmes/config.yaml`), launch the CLI briefly:

```bash
export PAGERDUTY_API_KEY="test-dummy"
poetry run holmes toolset list | grep -i pagerduty
```

Expected: `pagerduty` appears in the list. If the health check fails because the dummy key is invalid, that's fine — what matters is the toolset is registered and attempted.

- [ ] **Step 5: Final commit if anything changed**

```bash
git status
# if any residual edits from the smoke check:
git add -A && git commit -s --no-verify -m "chore: finalize pagerduty project-scoping"
```

---

## Acceptance Criteria Mapping

| Story criterion | Task |
|---|---|
| PagerDuty integration supports a valid API key | Task 1 (config retained), Task 8 (validated via test-connection) |
| API key behavior (global vs project-scoped) is validated and confirmed | Task 6 (health check), Task 10 (docs), Task 11 (eval proves scope) |
| HolmesGPT successfully initializes the PagerDuty toolset without API key errors | Task 6 (clearer error messages), Task 8 (Test Connection surfaces errors pre-chat) |
| Integration behavior is consistent with Datadog scoping | Task 7 (`PYTHON_TOOLSET_FACTORIES`), Task 9 (Instances UI), Task 1 (per-instance config fields mirror Datadog) |
