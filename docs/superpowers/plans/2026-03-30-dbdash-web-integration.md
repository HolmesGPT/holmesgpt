# DBADash-Web Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `dbdash` Python toolset to HolmesGPT that connects to dbdash-web to investigate SQL Server performance issues and triage alerts.

**Architecture:** A single Python toolset with 12 read-only tools, JWT authentication via `requests.Session`, tag-based instance filtering, and Jinja2 LLM instructions. Follows the established Grafana/Elasticsearch toolset pattern.

**Tech Stack:** Python 3.11+, requests, Pydantic v2, Jinja2, pytest

---

## File Structure

```
holmes/plugins/toolsets/dbdash/
├── __init__.py                  # Empty
├── common.py                    # DBADashConfig + DBADashClient (HTTP wrapper)
├── dbdash_toolset.py            # DBADashToolset class + health check
├── tools/
│   ├── __init__.py              # Empty
│   ├── instances.py             # ListInstances, GetInstanceDetails
│   ├── alerts.py                # GetActiveAlerts, GetClosedAlerts
│   ├── performance.py           # GetCpuMetrics, GetMemoryMetrics, GetWaitStats, GetIoStats
│   └── queries.py               # GetSlowQueries, GetRunningQueries, GetBlockingQueries, GetQueryStoreTop
├── instructions.jinja2          # LLM investigation workflow guidance
tests/
├── test_dbdash_common.py        # Config validation + HTTP client tests
├── test_dbdash_tools.py         # Tool invocation tests with mocked HTTP
└── test_dbdash_tag_filtering.py # Instance tag filtering logic tests
```

---

### Task 1: Config Model and HTTP Client (`common.py`)

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/__init__.py`
- Create: `holmes/plugins/toolsets/dbdash/common.py`
- Create: `tests/test_dbdash_common.py`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p holmes/plugins/toolsets/dbdash/tools
touch holmes/plugins/toolsets/dbdash/__init__.py
touch holmes/plugins/toolsets/dbdash/tools/__init__.py
```

- [ ] **Step 2: Write the failing test for DBADashConfig**

Create `tests/test_dbdash_common.py`:

```python
import pytest
from holmes.plugins.toolsets.dbdash.common import DBADashConfig


class TestDBADashConfig:
    def test_valid_config(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
        )
        assert config.api_url == "https://db-monitor.example.com"
        assert config.username == "holmes"
        assert config.password == "secret123"
        assert config.instance_tags is None
        assert config.verify_ssl is True
        assert config.timeout_seconds == 30

    def test_config_with_instance_tags(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
            instance_tags={"project": "payments", "environment": "production"},
        )
        assert config.instance_tags == {"project": "payments", "environment": "production"}

    def test_config_missing_required_fields(self):
        with pytest.raises(Exception):
            DBADashConfig(api_url="https://db-monitor.example.com")

    def test_config_strips_trailing_slash(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com/",
            username="holmes",
            password="secret123",
        )
        assert config.api_url == "https://db-monitor.example.com"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_common.py::TestDBADashConfig -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'holmes.plugins.toolsets.dbdash.common'`

- [ ] **Step 4: Implement DBADashConfig**

Create `holmes/plugins/toolsets/dbdash/common.py`:

```python
import logging
from typing import Any, Dict, Optional

import requests
from pydantic import Field, model_validator

from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


class DBADashConfig(ToolsetConfig):
    """Configuration for the DBADash Web toolset."""

    api_url: str = Field(
        title="DBADash Web URL",
        description="Base URL of the dbdash-web instance",
        examples=["https://db-monitor.shared.platform.pditechnologies.com"],
    )
    username: str = Field(
        title="Username",
        description="Username for JWT authentication",
        examples=["holmes-service"],
    )
    password: str = Field(
        title="Password",
        description="Password for JWT authentication",
        examples=["{{ env.DBDASH_PASSWORD }}"],
    )
    instance_tags: Optional[Dict[str, str]] = Field(
        default=None,
        title="Instance Tags",
        description="Filter instances by tags. Only instances matching ALL tags are visible.",
        examples=[{"project": "payments"}],
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates",
    )
    timeout_seconds: int = Field(
        default=30,
        title="Request Timeout",
        description="HTTP request timeout in seconds",
    )

    @model_validator(mode="after")
    def strip_trailing_slash(self) -> "DBADashConfig":
        if self.api_url.endswith("/"):
            self.api_url = self.api_url.rstrip("/")
        return self
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_dbdash_common.py::TestDBADashConfig -v --no-cov`
Expected: All 4 tests PASS

- [ ] **Step 6: Write the failing test for DBADashClient**

Append to `tests/test_dbdash_common.py`:

```python
from unittest.mock import MagicMock, patch
from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig


class TestDBADashClient:
    def _make_config(self) -> DBADashConfig:
        return DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
        )

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_login_sends_credentials(self, mock_session_cls):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user": {"username": "holmes"}}
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        client._login()

        mock_session.post.assert_called_once_with(
            "https://db-monitor.example.com/api/auth/login",
            json={"username": "holmes", "password": "secret123"},
            timeout=30,
            verify=True,
        )

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_get_request_triggers_login_on_first_call(self, mock_session_cls):
        mock_session = MagicMock()
        # Login response
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"user": {"username": "holmes"}}
        # GET response
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {"data": []}
        get_response.raise_for_status = MagicMock()
        mock_session.post.return_value = login_response
        mock_session.get.return_value = get_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        result = client.get("/api/instances")

        assert result == {"data": []}
        mock_session.post.assert_called_once()  # login
        mock_session.get.assert_called_once()   # GET /api/instances

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_get_retries_on_401(self, mock_session_cls):
        mock_session = MagicMock()
        # Login response
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"user": {"username": "holmes"}}
        # First GET returns 401
        response_401 = MagicMock()
        response_401.status_code = 401
        response_401.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=response_401
        )
        # Retry GET returns 200
        response_200 = MagicMock()
        response_200.status_code = 200
        response_200.json.return_value = {"data": "ok"}
        response_200.raise_for_status = MagicMock()

        mock_session.post.return_value = login_response
        mock_session.get.side_effect = [response_401, response_200]
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        result = client.get("/api/health")

        assert result == {"data": "ok"}
        assert mock_session.post.call_count == 2  # login + re-login
        assert mock_session.get.call_count == 2   # 401 + retry

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_login_failure_raises(self, mock_session_cls):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid credentials"
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        with pytest.raises(requests.exceptions.HTTPError):
            client._login()
```

- [ ] **Step 7: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_common.py::TestDBADashClient -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'DBADashClient'`

- [ ] **Step 8: Implement DBADashClient**

Append to `holmes/plugins/toolsets/dbdash/common.py`:

```python
class DBADashClient:
    """Thin HTTP wrapper for dbdash-web API with JWT authentication.

    Handles login, token caching via session cookies, and automatic
    re-authentication on 401 responses.
    """

    def __init__(self, config: DBADashConfig):
        self._config = config
        self._session: Optional[requests.Session] = None
        self._authenticated = False

    def _ensure_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = self._config.verify_ssl
        return self._session

    def _login(self) -> None:
        """Authenticate with dbdash-web and store JWT cookie in session."""
        session = self._ensure_session()
        response = session.post(
            f"{self._config.api_url}/api/auth/login",
            json={
                "username": self._config.username,
                "password": self._config.password,
            },
            timeout=self._config.timeout_seconds,
            verify=self._config.verify_ssl,
        )
        response.raise_for_status()
        self._authenticated = True
        logger.debug("Successfully authenticated with dbdash-web")

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self._login()

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Make an authenticated GET request to dbdash-web.

        Automatically logs in on first call and retries once on 401.

        Args:
            endpoint: API path (e.g., "/api/instances")
            params: Optional query parameters

        Returns:
            Parsed JSON response
        """
        self._ensure_authenticated()
        session = self._ensure_session()
        url = f"{self._config.api_url}{endpoint}"

        try:
            response = session.get(
                url,
                params=params,
                timeout=self._config.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.info("Got 401, re-authenticating with dbdash-web")
                self._authenticated = False
                self._login()
                response = session.get(
                    url,
                    params=params,
                    timeout=self._config.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            raise

    def health_check(self) -> Dict[str, Any]:
        """Login and call /api/health to verify connectivity."""
        return self.get("/api/health")
```

- [ ] **Step 9: Run all tests to verify they pass**

Run: `poetry run pytest tests/test_dbdash_common.py -v --no-cov`
Expected: All 8 tests PASS

- [ ] **Step 10: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/__init__.py holmes/plugins/toolsets/dbdash/tools/__init__.py holmes/plugins/toolsets/dbdash/common.py tests/test_dbdash_common.py
git commit -s --no-verify -m "feat(dbdash): add config model and HTTP client with JWT auth"
```

---

### Task 2: Tag Filtering Logic

**Files:**
- Modify: `holmes/plugins/toolsets/dbdash/common.py`
- Create: `tests/test_dbdash_tag_filtering.py`

- [ ] **Step 1: Write the failing tests for tag filtering**

Create `tests/test_dbdash_tag_filtering.py`:

```python
from holmes.plugins.toolsets.dbdash.common import filter_instances_by_tags


class TestFilterInstancesByTags:
    def test_no_tags_configured_returns_all(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "prod-sql-02"},
        ]
        instance_tags = []
        result = filter_instances_by_tags(instances, instance_tags, configured_tags=None)
        assert len(result) == 2

    def test_single_tag_filters_correctly(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "staging-sql-01"},
            {"InstanceID": 3, "InstanceDisplayName": "prod-sql-02"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "payments"},
            {"InstanceID": 2, "TagName": "project", "TagValue": "staging"},
            {"InstanceID": 3, "TagName": "project", "TagValue": "payments"},
        ]
        result = filter_instances_by_tags(
            instances, instance_tags, configured_tags={"project": "payments"}
        )
        assert len(result) == 2
        assert result[0]["InstanceID"] == 1
        assert result[1]["InstanceID"] == 3

    def test_multiple_tags_require_all_match(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "prod-sql-02"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "payments"},
            {"InstanceID": 1, "TagName": "environment", "TagValue": "production"},
            {"InstanceID": 2, "TagName": "project", "TagValue": "payments"},
            {"InstanceID": 2, "TagName": "environment", "TagValue": "staging"},
        ]
        result = filter_instances_by_tags(
            instances,
            instance_tags,
            configured_tags={"project": "payments", "environment": "production"},
        )
        assert len(result) == 1
        assert result[0]["InstanceID"] == 1

    def test_no_matching_instances_returns_empty(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "logistics"},
        ]
        result = filter_instances_by_tags(
            instances, instance_tags, configured_tags={"project": "payments"}
        )
        assert len(result) == 0

    def test_instance_without_tags_excluded_when_tags_configured(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "untagged-sql"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "payments"},
        ]
        result = filter_instances_by_tags(
            instances, instance_tags, configured_tags={"project": "payments"}
        )
        assert len(result) == 1
        assert result[0]["InstanceID"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_tag_filtering.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'filter_instances_by_tags'`

- [ ] **Step 3: Implement filter_instances_by_tags**

Add to `holmes/plugins/toolsets/dbdash/common.py` (after the `DBADashConfig` class, before `DBADashClient`):

```python
def filter_instances_by_tags(
    instances: list[Dict[str, Any]],
    instance_tags: list[Dict[str, Any]],
    configured_tags: Optional[Dict[str, str]],
) -> list[Dict[str, Any]]:
    """Filter instances to only those matching ALL configured tags.

    Args:
        instances: List of {"InstanceID": int, "InstanceDisplayName": str}
        instance_tags: List of {"InstanceID": int, "TagName": str, "TagValue": str}
        configured_tags: Tags to filter by (e.g., {"project": "payments"}).
                         If None, returns all instances.

    Returns:
        Filtered list of instances matching all configured tags.
    """
    if not configured_tags:
        return instances

    # Build a map: instance_id -> {tag_name: tag_value}
    tag_map: Dict[int, Dict[str, str]] = {}
    for tag_entry in instance_tags:
        instance_id = tag_entry["InstanceID"]
        if instance_id not in tag_map:
            tag_map[instance_id] = {}
        tag_map[instance_id][tag_entry["TagName"]] = tag_entry["TagValue"]

    # Filter instances where ALL configured tags match
    filtered = []
    for instance in instances:
        instance_id = instance["InstanceID"]
        instance_tag_values = tag_map.get(instance_id, {})
        if all(
            instance_tag_values.get(tag_name) == tag_value
            for tag_name, tag_value in configured_tags.items()
        ):
            filtered.append(instance)

    return filtered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_dbdash_tag_filtering.py -v --no-cov`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/common.py tests/test_dbdash_tag_filtering.py
git commit -s --no-verify -m "feat(dbdash): add tag-based instance filtering logic"
```

---

### Task 3: Instance Discovery Tools

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/tools/instances.py`
- Create: `tests/test_dbdash_tools.py`

- [ ] **Step 1: Write the failing test for ListInstances**

Create `tests/test_dbdash_tools.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext
from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig


def make_mock_toolset(instance_tags=None):
    """Create a mock toolset with a mock client."""
    config = DBADashConfig(
        api_url="https://db-monitor.example.com",
        username="holmes",
        password="secret123",
        instance_tags=instance_tags,
    )
    toolset = MagicMock()
    toolset.config = config
    toolset.client = MagicMock(spec=DBADashClient)
    return toolset


def make_context():
    return ToolInvokeContext()


class TestListInstances:
    def test_returns_all_instances_when_no_tags(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import ListInstances

        toolset = make_mock_toolset(instance_tags=None)
        toolset.client.get.side_effect = [
            # /api/instances
            [
                {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
                {"InstanceID": 2, "InstanceDisplayName": "prod-sql-02"},
            ],
        ]

        tool = ListInstances(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data) == 2

    def test_filters_by_tags_when_configured(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import ListInstances

        toolset = make_mock_toolset(instance_tags={"project": "payments"})
        toolset.client.get.side_effect = [
            # /api/instances
            [
                {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
                {"InstanceID": 2, "InstanceDisplayName": "staging-sql-01"},
            ],
            # /api/settings/tags
            {
                "instanceTags": [
                    {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01", "TagName": "project", "TagValue": "payments"},
                    {"InstanceID": 2, "InstanceDisplayName": "staging-sql-01", "TagName": "project", "TagValue": "staging"},
                ],
            },
        ]

        tool = ListInstances(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data) == 1
        assert result.data[0]["InstanceID"] == 1

    def test_returns_no_data_when_no_instances_match(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import ListInstances

        toolset = make_mock_toolset(instance_tags={"project": "nonexistent"})
        toolset.client.get.side_effect = [
            [{"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"}],
            {"instanceTags": [{"InstanceID": 1, "InstanceDisplayName": "prod-sql-01", "TagName": "project", "TagValue": "payments"}]},
        ]

        tool = ListInstances(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.NO_DATA


class TestGetInstanceDetails:
    def test_returns_instance_details(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import GetInstanceDetails

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "instance": {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            "hardware": [{"Name": "CPU", "Value": "8 cores"}],
            "collectionDates": [{"CollectionType": "CPU", "LastCollected": "2026-03-30T10:00:00Z"}],
        }

        tool = GetInstanceDetails(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        toolset.client.get.assert_called_once_with("/api/instances/1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_tools.py::TestListInstances -v --no-cov`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement instance tools**

Create `holmes/plugins/toolsets/dbdash/tools/instances.py`:

```python
from typing import TYPE_CHECKING, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.dbdash.common import filter_instances_by_tags
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset


class ListInstances(Tool):
    """List SQL Server instances, filtered by configured tags."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_list_instances",
            description=(
                "List available SQL Server instances monitored by DBADash. "
                "Returns instance IDs and display names. If tags are configured, "
                "only instances matching those tags are returned."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            instances = self._toolset.client.get("/api/instances")

            configured_tags = self._toolset.config.instance_tags
            if configured_tags:
                tags_response = self._toolset.client.get("/api/settings/tags")
                instance_tags = tags_response.get("instanceTags", [])
                instances = filter_instances_by_tags(instances, instance_tags, configured_tags)

            if not instances:
                tag_desc = f" matching tags {configured_tags}" if configured_tags else ""
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No SQL Server instances found{tag_desc}.",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=instances,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list instances from {self._toolset.config.api_url}/api/instances: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List Instances"


class GetInstanceDetails(Tool):
    """Get detailed information about a specific SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_instance_details",
            description=(
                "Get detailed information about a SQL Server instance including "
                "hardware specs, configuration, and collection dates."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (from dbdash_list_instances)",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instance_id = params.get("instanceId")
        if not instance_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter: instanceId",
                params=params,
            )

        try:
            data = self._toolset.client.get(f"/api/instances/{instance_id}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to get instance details from "
                    f"{self._toolset.config.api_url}/api/instances/{instance_id}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Instance {params.get('instanceId', '')}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_dbdash_tools.py -v --no-cov`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/tools/instances.py tests/test_dbdash_tools.py
git commit -s --no-verify -m "feat(dbdash): add instance discovery tools with tag filtering"
```

---

### Task 4: Alert Tools

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/tools/alerts.py`
- Modify: `tests/test_dbdash_tools.py`

- [ ] **Step 1: Write the failing tests for alert tools**

Append to `tests/test_dbdash_tools.py`:

```python
class TestGetActiveAlerts:
    def test_returns_active_alerts(self):
        from holmes.plugins.toolsets.dbdash.tools.alerts import GetActiveAlerts

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "alerts": [
                {
                    "Priority": 1,
                    "AlertType": "CPU",
                    "AlertKey": "prod-sql-01",
                    "InstanceDisplayName": "prod-sql-01",
                    "FirstMessage": "CPU > 90%",
                    "TriggerDate": "2026-03-30T10:00:00Z",
                    "UpdateCount": 3,
                    "IsAcknowledged": False,
                },
            ],
            "counts": {"critical": 1, "warning": 0, "info": 0, "acknowledged": 0},
        }

        tool = GetActiveAlerts(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data["alerts"]) == 1
        toolset.client.get.assert_called_once_with("/api/alerts", params={"status": "active"})

    def test_returns_no_data_when_no_alerts(self):
        from holmes.plugins.toolsets.dbdash.tools.alerts import GetActiveAlerts

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "alerts": [],
            "counts": {"critical": 0, "warning": 0, "info": 0, "acknowledged": 0},
        }

        tool = GetActiveAlerts(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.NO_DATA


class TestGetClosedAlerts:
    def test_returns_closed_alerts(self):
        from holmes.plugins.toolsets.dbdash.tools.alerts import GetClosedAlerts

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "alerts": [
                {
                    "AlertType": "Memory",
                    "InstanceDisplayName": "prod-sql-01",
                    "ClosedDate": "2026-03-30T09:00:00Z",
                },
            ],
        }

        tool = GetClosedAlerts(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        toolset.client.get.assert_called_once_with("/api/alerts", params={"status": "closed"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_tools.py::TestGetActiveAlerts -v --no-cov`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement alert tools**

Create `holmes/plugins/toolsets/dbdash/tools/alerts.py`:

```python
from typing import TYPE_CHECKING, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset


class GetActiveAlerts(Tool):
    """Get active alerts from DBADash."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_active_alerts",
            description=(
                "Get active alerts from DBADash including priority, alert type, "
                "affected instance, message, and trigger date. Also returns alert "
                "counts by severity (critical, warning, info)."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.client.get("/api/alerts", params={"status": "active"})
            alerts = data.get("alerts", [])

            if not alerts:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error="No active alerts found.",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch active alerts from {self._toolset.config.api_url}/api/alerts: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Active Alerts"


class GetClosedAlerts(Tool):
    """Get recently closed alerts from DBADash."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_closed_alerts",
            description=(
                "Get recently closed alerts from DBADash for historical context. "
                "Useful for understanding patterns and recent resolutions."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.client.get("/api/alerts", params={"status": "closed"})
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch closed alerts from {self._toolset.config.api_url}/api/alerts: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Closed Alerts"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_dbdash_tools.py::TestGetActiveAlerts tests/test_dbdash_tools.py::TestGetClosedAlerts -v --no-cov`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/tools/alerts.py tests/test_dbdash_tools.py
git commit -s --no-verify -m "feat(dbdash): add alert triage tools"
```

---

### Task 5: Performance Tools

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/tools/performance.py`
- Modify: `tests/test_dbdash_tools.py`

- [ ] **Step 1: Write the failing tests for performance tools**

Append to `tests/test_dbdash_tools.py`:

```python
class TestGetCpuMetrics:
    def test_returns_cpu_data(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetCpuMetrics

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"EventTime": "2026-03-30T10:00:00Z", "SQLProcessCPU": 85, "OtherCPU": 5, "MaxCPU": 90}],
            "histogram": [{"CPUBucket": 90, "OccurrenceCount": 15}],
        }

        tool = GetCpuMetrics(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        toolset.client.get.assert_called_once()
        call_args = toolset.client.get.call_args
        assert call_args[0][0] == "/api/performance/cpu"
        assert call_args[1]["params"]["instanceId"] == "1"

    def test_returns_no_data_when_empty(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetCpuMetrics

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {"data": [], "histogram": []}

        tool = GetCpuMetrics(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.NO_DATA

    def test_missing_instance_id_returns_error(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetCpuMetrics

        toolset = make_mock_toolset()
        tool = GetCpuMetrics(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.ERROR


class TestGetWaitStats:
    def test_returns_wait_data(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetWaitStats

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"Time": "2026-03-30T10:00:00Z", "WaitType": "PAGEIOLATCH_SH", "TotalWaitSec": 120}],
            "summary": [{"WaitType": "PAGEIOLATCH_SH", "Description": "I/O wait", "TotalWaitSec": 120}],
        }

        tool = GetWaitStats(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_tools.py::TestGetCpuMetrics -v --no-cov`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement performance tools**

Create `holmes/plugins/toolsets/dbdash/tools/performance.py`:

```python
import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

# Default time range: last 24 hours
DEFAULT_HOURS = 24


def _default_time_range() -> tuple[str, str]:
    """Return (from, to) ISO strings for the last 24 hours."""
    now = datetime.datetime.now(datetime.timezone.utc)
    from_time = now - datetime.timedelta(hours=DEFAULT_HOURS)
    return from_time.isoformat(), now.isoformat()


def _build_time_params(params: dict) -> Dict[str, Any]:
    """Build query params with instanceId and time range."""
    instance_id = params.get("instanceId")
    if not instance_id:
        raise ValueError("Missing required parameter: instanceId")

    from_time = params.get("from")
    to_time = params.get("to")
    if not from_time or not to_time:
        default_from, default_to = _default_time_range()
        from_time = from_time or default_from
        to_time = to_time or default_to

    return {
        "instanceId": instance_id,
        "from": from_time,
        "to": to_time,
    }


_TIME_RANGE_PARAMS = {
    "instanceId": ToolParameter(
        description="The instance ID (from dbdash_list_instances)",
        type="string",
        required=True,
    ),
    "from": ToolParameter(
        description="Start datetime in ISO 8601 format (e.g., 2026-03-30T00:00:00Z). Defaults to 24 hours ago.",
        type="string",
        required=False,
    ),
    "to": ToolParameter(
        description="End datetime in ISO 8601 format. Defaults to now.",
        type="string",
        required=False,
    ),
}


class GetCpuMetrics(Tool):
    """Get CPU usage metrics for a SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_cpu_metrics",
            description=(
                "Get CPU usage over time for a SQL Server instance. Returns SQL process CPU, "
                "other CPU, and max CPU values, plus a histogram of CPU usage distribution."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/cpu", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No CPU data for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to fetch CPU metrics from "
                    f"{self._toolset.config.api_url}/api/performance/cpu "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: CPU Metrics for instance {params.get('instanceId', '?')}"


class GetMemoryMetrics(Tool):
    """Get memory usage metrics for a SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_memory_metrics",
            description=(
                "Get memory usage over time for a SQL Server instance. Returns total server memory, "
                "target memory, free memory, and memory clerk breakdown."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/memory", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No memory data for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to fetch memory metrics from "
                    f"{self._toolset.config.api_url}/api/performance/memory "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Memory Metrics for instance {params.get('instanceId', '?')}"


class GetWaitStats(Tool):
    """Get wait statistics for a SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_wait_stats",
            description=(
                "Get wait statistics for a SQL Server instance. Returns time series of wait types "
                "and a summary with total/signal wait times. Critical for identifying bottleneck types "
                "(I/O, locks, memory, CPU, network)."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/waits", params=query_params)
            if not data.get("data") and not data.get("summary"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No wait stats for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to fetch wait stats from "
                    f"{self._toolset.config.api_url}/api/performance/waits "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Wait Stats for instance {params.get('instanceId', '?')}"


class GetIoStats(Tool):
    """Get I/O statistics for a SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_io_stats",
            description=(
                "Get I/O statistics for a SQL Server instance. Returns time series of read/write "
                "latency, throughput (MB/s), and IOPS, plus per-database and per-filegroup summaries."
            ),
            parameters=dict(_TIME_RANGE_PARAMS),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        try:
            data = self._toolset.client.get("/api/performance/io", params=query_params)
            if not data.get("timeSeries"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=(
                        f"No I/O data for instance {query_params['instanceId']} "
                        f"between {query_params['from']} and {query_params['to']}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to fetch I/O stats from "
                    f"{self._toolset.config.api_url}/api/performance/io "
                    f"with params {query_params}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: I/O Stats for instance {params.get('instanceId', '?')}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_dbdash_tools.py::TestGetCpuMetrics tests/test_dbdash_tools.py::TestGetWaitStats -v --no-cov`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/tools/performance.py tests/test_dbdash_tools.py
git commit -s --no-verify -m "feat(dbdash): add performance monitoring tools (CPU, memory, waits, I/O)"
```

---

### Task 6: Query Tools

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/tools/queries.py`
- Modify: `tests/test_dbdash_tools.py`

- [ ] **Step 1: Write the failing tests for query tools**

Append to `tests/test_dbdash_tools.py`:

```python
class TestGetSlowQueries:
    def test_returns_slow_queries(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetSlowQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "summary": [{"Grp": "Total", "Total": 50, "TotalDurationMs": 120000}],
            "detail": [{"InstanceDisplayName": "prod-sql-01", "SQLText": "SELECT *", "Duration": 5000}],
        }

        tool = GetSlowQueries(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS


class TestGetRunningQueries:
    def test_returns_running_queries(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetRunningQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"SPID": 55, "DatabaseName": "PaymentDB", "Duration": 120, "SQLText": "UPDATE orders..."}],
        }

        tool = GetRunningQueries(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_blocked_only_filter(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetRunningQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {"data": []}

        tool = GetRunningQueries(toolset)
        result = tool._invoke({"instanceId": "1", "blockedOnly": "true"}, make_context())

        call_params = toolset.client.get.call_args[1]["params"]
        assert call_params["blockedOnly"] == "true"


class TestGetBlockingQueries:
    def test_returns_blocking_data(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetBlockingQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"HeadBlockerSPID": 55, "BlockedSPID": 60, "WaitType": "LCK_M_X"}],
            "summary": [{"WaitType": "LCK_M_X", "BlockingCount": 5}],
            "snapshots": [],
        }

        tool = GetBlockingQueries(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS


class TestGetQueryStoreTop:
    def test_returns_top_queries(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetQueryStoreTop

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "databases": [{"DatabaseID": 1, "name": "PaymentDB"}],
            "data": [{"QueryID": 42, "QueryText": "SELECT * FROM orders", "TotalCPU": 50000}],
            "metric": "cpu",
        }

        tool = GetQueryStoreTop(toolset)
        result = tool._invoke({"instanceId": "1", "metric": "cpu"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        call_params = toolset.client.get.call_args[1]["params"]
        assert call_params["metric"] == "cpu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_dbdash_tools.py::TestGetSlowQueries -v --no-cov`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement query tools**

Create `holmes/plugins/toolsets/dbdash/tools/queries.py`:

```python
from typing import TYPE_CHECKING, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.dbdash.tools.performance import (
    _TIME_RANGE_PARAMS,
    _build_time_params,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset


class GetSlowQueries(Tool):
    """Get slow queries from DBADash."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_slow_queries",
            description=(
                "Get slow queries for a SQL Server instance. Returns a summary of query "
                "duration distribution and detailed list of slow queries with SQL text, "
                "duration, CPU, reads, and execution context."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (from dbdash_list_instances). Optional - omit to see all instances.",
                    type="string",
                    required=False,
                ),
                "from": ToolParameter(
                    description="Start datetime in ISO 8601 format. Defaults to 24 hours ago.",
                    type="string",
                    required=False,
                ),
                "to": ToolParameter(
                    description="End datetime in ISO 8601 format. Defaults to now.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query_params = _build_time_params(params)
        except ValueError:
            # instanceId is optional for slow queries
            from holmes.plugins.toolsets.dbdash.tools.performance import _default_time_range

            default_from, default_to = _default_time_range()
            query_params = {
                "from": params.get("from", default_from),
                "to": params.get("to", default_to),
            }
            if params.get("instanceId"):
                query_params["instanceId"] = params["instanceId"]

        try:
            data = self._toolset.client.get("/api/queries/slow", params=query_params)
            if not data.get("detail") and not data.get("summary"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No slow queries found for params {query_params}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch slow queries from {self._toolset.config.api_url}/api/queries/slow: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Slow Queries"


class GetRunningQueries(Tool):
    """Get currently running queries on a SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_running_queries",
            description=(
                "Get currently running queries on a SQL Server instance. Returns SPID, "
                "database, login, status, duration, wait type, blocking info, and SQL text."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (required)",
                    type="string",
                    required=True,
                ),
                "minDuration": ToolParameter(
                    description="Minimum duration in seconds to filter queries",
                    type="string",
                    required=False,
                ),
                "blockedOnly": ToolParameter(
                    description="Set to 'true' to show only blocked queries",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instance_id = params.get("instanceId")
        if not instance_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter: instanceId",
                params=params,
            )

        query_params: Dict = {"instanceId": instance_id}
        if params.get("minDuration"):
            query_params["minDuration"] = params["minDuration"]
        if params.get("blockedOnly"):
            query_params["blockedOnly"] = params["blockedOnly"]

        try:
            data = self._toolset.client.get("/api/queries/running", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No running queries on instance {instance_id}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch running queries from {self._toolset.config.api_url}/api/queries/running: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Running Queries on instance {params.get('instanceId', '?')}"


class GetBlockingQueries(Tool):
    """Get blocking query chains from DBADash."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_blocking_queries",
            description=(
                "Get blocking query chains for a SQL Server instance. Returns head blockers, "
                "blocked sessions, wait types, durations, and SQL text. Also includes a summary "
                "of blocking patterns and snapshot history."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID. Optional - omit to see all instances.",
                    type="string",
                    required=False,
                ),
                "from": ToolParameter(
                    description="Start datetime in ISO 8601 format. Defaults to 24 hours ago.",
                    type="string",
                    required=False,
                ),
                "to": ToolParameter(
                    description="End datetime in ISO 8601 format. Defaults to now.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        from holmes.plugins.toolsets.dbdash.tools.performance import _default_time_range

        default_from, default_to = _default_time_range()
        query_params: Dict = {
            "from": params.get("from", default_from),
            "to": params.get("to", default_to),
        }
        if params.get("instanceId"):
            query_params["instanceId"] = params["instanceId"]

        try:
            data = self._toolset.client.get("/api/queries/blocking", params=query_params)
            if not data.get("data") and not data.get("summary"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No blocking queries found for params {query_params}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch blocking queries from {self._toolset.config.api_url}/api/queries/blocking: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Blocking Queries"


class GetQueryStoreTop(Tool):
    """Get top queries from Query Store."""

    def __init__(self, toolset: "DBADashToolset"):
        self._toolset = toolset
        super().__init__(
            name="dbdash_get_query_store_top",
            description=(
                "Get top resource-consuming queries from Query Store for a SQL Server instance. "
                "Can sort by CPU, duration, execution count, memory, logical I/O, or physical I/O."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (required)",
                    type="string",
                    required=True,
                ),
                "databaseId": ToolParameter(
                    description="Filter to a specific database ID",
                    type="string",
                    required=False,
                ),
                "metric": ToolParameter(
                    description="Sort metric: cpu, duration, execution_count, memory, logical_io, physical_io",
                    type="string",
                    required=False,
                    enum=["cpu", "duration", "execution_count", "memory", "logical_io", "physical_io"],
                ),
                "top": ToolParameter(
                    description="Number of top queries to return (default: 100)",
                    type="string",
                    required=False,
                ),
                "from": ToolParameter(
                    description="Start datetime in ISO 8601 format. Defaults to 24 hours ago.",
                    type="string",
                    required=False,
                ),
                "to": ToolParameter(
                    description="End datetime in ISO 8601 format. Defaults to now.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instance_id = params.get("instanceId")
        if not instance_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter: instanceId",
                params=params,
            )

        from holmes.plugins.toolsets.dbdash.tools.performance import _default_time_range

        default_from, default_to = _default_time_range()
        query_params: Dict = {
            "instanceId": instance_id,
            "from": params.get("from", default_from),
            "to": params.get("to", default_to),
        }
        if params.get("databaseId"):
            query_params["databaseId"] = params["databaseId"]
        if params.get("metric"):
            query_params["metric"] = params["metric"]
        if params.get("top"):
            query_params["top"] = params["top"]

        try:
            data = self._toolset.client.get("/api/queries/query-store", params=query_params)
            if not data.get("data"):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No Query Store data for instance {instance_id}.",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch Query Store data from {self._toolset.config.api_url}/api/queries/query-store: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        metric = params.get("metric", "cpu")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Top Queries by {metric}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_dbdash_tools.py -v --no-cov`
Expected: All tests PASS (should be ~15 tests total)

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/tools/queries.py tests/test_dbdash_tools.py
git commit -s --no-verify -m "feat(dbdash): add query analysis tools (slow, running, blocking, query store)"
```

---

### Task 7: LLM Instructions Template

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/instructions.jinja2`

- [ ] **Step 1: Create the LLM instructions template**

Create `holmes/plugins/toolsets/dbdash/instructions.jinja2`:

```
---
## DBADash SQL Server Investigation Guide

You have access to DBADash tools for investigating SQL Server database issues.
{% if instance_tags %}
**Scope:** Only instances matching tags {{ instance_tags }} are visible.
{% endif %}

### Investigation Workflow

**Step 1 — Discover instances:**
Always start by calling `dbdash_list_instances` to find available SQL Server instances.
Note the InstanceID values — you'll need them for all subsequent tool calls.

**Step 2 — Check alerts (if investigating an alert or unknown issue):**
- Call `dbdash_get_active_alerts` to see current alerts
- Cross-reference alert instance names with your instance list
- Call `dbdash_get_closed_alerts` for recent patterns

**Step 3 — Diagnose performance:**
Follow this diagnostic ladder:

1. **CPU** — Call `dbdash_get_cpu_metrics` first
   - High SQLProcessCPU → query-driven CPU pressure
   - High OtherCPU → external process or OS-level issue

2. **Wait Stats** — Call `dbdash_get_wait_stats` to identify the bottleneck type:
   - `PAGEIOLATCH_*` → I/O bottleneck → check `dbdash_get_io_stats`
   - `LCK_M_*` → Lock contention → check `dbdash_get_blocking_queries`
   - `RESOURCE_SEMAPHORE` → Memory pressure → check `dbdash_get_memory_metrics`
   - `CXPACKET` / `CXCONSUMER` → Parallelism issues → check query plans
   - `SOS_SCHEDULER_YIELD` → CPU pressure → check `dbdash_get_query_store_top` with metric=cpu

3. **Drill into the bottleneck:**
   - I/O: `dbdash_get_io_stats` — look at ReadLatency, WriteLatency per database
   - Memory: `dbdash_get_memory_metrics` — compare Total vs Target memory, check clerks
   - Blocking: `dbdash_get_blocking_queries` — identify head blockers and wait resources
   - Queries: `dbdash_get_slow_queries` — find the worst offenders

4. **Identify culprit queries:**
   - `dbdash_get_query_store_top` with the relevant metric (cpu, duration, logical_io)
   - `dbdash_get_running_queries` for currently executing long-running queries

**Step 4 — Correlate findings:**
- High CPU + top Query Store queries → identify the specific query causing CPU pressure
- Blocking chains + slow queries → identify the head blocker query and its lock type
- Memory pressure + wait stats → confirm RESOURCE_SEMAPHORE waits correlate with low free memory

### Tips
- Default time range is 24 hours. Narrow it to the incident window for better signal.
- Use `dbdash_get_running_queries` with `blockedOnly=true` when investigating active blocking.
- Query Store `metric` parameter should match your investigation focus.
- Instance IDs are integers — always get them from `dbdash_list_instances` first.
---
```

- [ ] **Step 2: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/instructions.jinja2
git commit -s --no-verify -m "feat(dbdash): add LLM investigation instructions template"
```

---

### Task 8: Main Toolset Class and Registration

**Files:**
- Create: `holmes/plugins/toolsets/dbdash/dbdash_toolset.py`
- Modify: `holmes/plugins/toolsets/__init__.py`

- [ ] **Step 1: Create the main toolset class**

Create `holmes/plugins/toolsets/dbdash/dbdash_toolset.py`:

```python
import logging
import os
from typing import Any, ClassVar, Tuple, Type

from holmes.core.tools import CallablePrerequisite, Toolset, ToolsetTag
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig
from holmes.plugins.toolsets.dbdash.tools.alerts import GetActiveAlerts, GetClosedAlerts
from holmes.plugins.toolsets.dbdash.tools.instances import (
    GetInstanceDetails,
    ListInstances,
)
from holmes.plugins.toolsets.dbdash.tools.performance import (
    GetCpuMetrics,
    GetIoStats,
    GetMemoryMetrics,
    GetWaitStats,
)
from holmes.plugins.toolsets.dbdash.tools.queries import (
    GetBlockingQueries,
    GetQueryStoreTop,
    GetRunningQueries,
    GetSlowQueries,
)

logger = logging.getLogger(__name__)


class DBADashToolset(Toolset):
    """Toolset for investigating SQL Server issues via DBADash Web."""

    config_classes: ClassVar[list[Type[DBADashConfig]]] = [DBADashConfig]

    def __init__(self, name: str = "dbdash"):
        # Initialize client as None — created during prerequisites check
        self._dbdash_config: DBADashConfig | None = None
        self._client: DBADashClient | None = None

        super().__init__(
            name=name,
            description="Investigate SQL Server performance issues and alerts via DBADash Web",
            icon_url=None,
            docs_url=None,
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListInstances(self),
                GetInstanceDetails(self),
                GetActiveAlerts(self),
                GetClosedAlerts(self),
                GetCpuMetrics(self),
                GetMemoryMetrics(self),
                GetWaitStats(self),
                GetIoStats(self),
                GetSlowQueries(self),
                GetRunningQueries(self),
                GetBlockingQueries(self),
                GetQueryStoreTop(self),
            ],
            tags=[ToolsetTag.CORE],
            enabled=False,
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    @property
    def config(self) -> DBADashConfig:
        if self._dbdash_config is None:
            raise RuntimeError("DBADash toolset not initialized — config is None")
        return self._dbdash_config

    @property
    def client(self) -> DBADashClient:
        if self._client is None:
            raise RuntimeError("DBADash toolset not initialized — client is None")
        return self._client

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            logger.debug("DBADash config not provided for %s", self.name)
            return False, TOOLSET_CONFIG_MISSING_ERROR

        try:
            self._dbdash_config = DBADashConfig(**config)
            self._client = DBADashClient(self._dbdash_config)
            self._client.health_check()
            return True, ""
        except Exception as e:
            logger.exception("Failed to set up DBADash toolset %s", self.name)
            return False, str(e)
```

- [ ] **Step 2: Register the toolset in `__init__.py`**

Add the import to `holmes/plugins/toolsets/__init__.py` after the existing imports (around line 61, after the ServiceNow import):

```python
from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset
```

Add `DBADashToolset()` to the `load_python_toolsets` function's toolset list (around line 127, before the closing `]`):

```python
        DBADashToolset(),
```

Add to `PYTHON_TOOLSET_FACTORIES` dict (around line 271, before the closing `}`):

```python
    "dbdash": DBADashToolset,
```

- [ ] **Step 3: Run all tests to verify nothing is broken**

Run: `poetry run pytest tests/test_dbdash_common.py tests/test_dbdash_tag_filtering.py tests/test_dbdash_tools.py -v --no-cov`
Expected: All tests PASS

- [ ] **Step 4: Verify the toolset loads without errors**

Run: `poetry run python -c "from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset; t = DBADashToolset(); print(f'Toolset: {t.name}, Tools: {len(t.tools)}')"`
Expected: `Toolset: dbdash, Tools: 12`

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/dbdash/dbdash_toolset.py holmes/plugins/toolsets/__init__.py
git commit -s --no-verify -m "feat(dbdash): register DBADash toolset with 12 investigation tools"
```

---

### Task 9: End-to-End Smoke Test

**Files:**
- Modify: `tests/test_dbdash_tools.py`

- [ ] **Step 1: Write an integration-style test that exercises the full toolset**

Append to `tests/test_dbdash_tools.py`:

```python
class TestDBADashToolsetIntegration:
    """Smoke test: verify the toolset initializes and all tools are wired correctly."""

    def test_toolset_has_all_12_tools(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset()
        assert len(toolset.tools) == 12

        tool_names = {t.name for t in toolset.tools}
        expected_names = {
            "dbdash_list_instances",
            "dbdash_get_instance_details",
            "dbdash_get_active_alerts",
            "dbdash_get_closed_alerts",
            "dbdash_get_cpu_metrics",
            "dbdash_get_memory_metrics",
            "dbdash_get_wait_stats",
            "dbdash_get_io_stats",
            "dbdash_get_slow_queries",
            "dbdash_get_running_queries",
            "dbdash_get_blocking_queries",
            "dbdash_get_query_store_top",
        }
        assert tool_names == expected_names

    def test_toolset_name_and_description(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset()
        assert toolset.name == "dbdash"
        assert "SQL Server" in toolset.description

    def test_toolset_custom_name(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset(name="dbdash:payments")
        assert toolset.name == "dbdash:payments"

    def test_prerequisites_fail_without_config(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset()
        success, error = toolset.prerequisites_callable({})
        assert success is False
        assert "missing" in error.lower()

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_prerequisites_succeed_with_valid_config(self, mock_session_cls):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        mock_session = MagicMock()
        # Login response
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"user": {"username": "holmes"}}
        # Health response
        health_response = MagicMock()
        health_response.status_code = 200
        health_response.json.return_value = {"status": "connected"}
        health_response.raise_for_status = MagicMock()
        mock_session.post.return_value = login_response
        mock_session.get.return_value = health_response
        mock_session_cls.return_value = mock_session

        toolset = DBADashToolset()
        success, error = toolset.prerequisites_callable({
            "api_url": "https://db-monitor.example.com",
            "username": "holmes",
            "password": "secret123",
        })

        assert success is True
        assert error == ""
```

- [ ] **Step 2: Run all tests**

Run: `poetry run pytest tests/test_dbdash_common.py tests/test_dbdash_tag_filtering.py tests/test_dbdash_tools.py -v --no-cov`
Expected: All tests PASS (~20+ tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_dbdash_tools.py
git commit -s --no-verify -m "test(dbdash): add end-to-end smoke tests for toolset initialization"
```

---

### Task 10: Final Verification

- [ ] **Step 1: Run the full non-LLM test suite to verify no regressions**

Run: `poetry run pytest tests -m "not llm" --no-cov -x -q`
Expected: All existing tests still pass, plus the new dbdash tests

- [ ] **Step 2: Verify the toolset appears in the toolset list**

Run: `poetry run python -c "from holmes.plugins.toolsets import load_python_toolsets; ts = load_python_toolsets(dal=None); print([t.name for t in ts if 'dbdash' in t.name])"`
Expected: `['dbdash']`

- [ ] **Step 3: Verify config example generation**

Run: `poetry run python -c "from holmes.plugins.toolsets.dbdash.common import DBADashConfig; from holmes.utils.pydantic_utils import build_config_example; print(build_config_example(DBADashConfig))"`
Expected: Dict with example values for api_url, username, password, instance_tags

- [ ] **Step 4: Final commit if any cleanup was needed**

If any fixes were made during verification:
```bash
git add -A
git commit -s --no-verify -m "fix(dbdash): address issues found during final verification"
```
