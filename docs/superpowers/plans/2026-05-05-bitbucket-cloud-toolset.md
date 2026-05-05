# Bitbucket Cloud Python Toolset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only Bitbucket Cloud Python toolset so HolmesGPT projects can correlate incidents with code, PRs, and commits via per-project instances.

**Architecture:** New `BitbucketToolset` follows the established `dbdash` / `pagerduty` patterns — thin `requests`-based wrapper around Bitbucket Cloud REST API v2.0 with Bearer token auth. Ten tools covering repos, PRs (+ diffs + comments), commits (+ diffs), and file contents. Per-project scoping via a required `workspace` (in the secret) plus an optional `repositories` allowlist (in the instance config), enforced in Python before any API call. Size guards on diffs (200 KB) and file contents (2000 lines) protect the LLM context window.

**Tech Stack:** Python 3.11+, Pydantic v2, `requests`, pytest + `responses` for HTTP mocking, React 18 + TypeScript (UI), OpenTofu (no infra changes in this plan).

**Spec:** `docs/superpowers/specs/2026-05-05-bitbucket-cloud-toolset-design.md`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `holmes/plugins/toolsets/bitbucket/__init__.py` | Create | Empty package marker |
| `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py` | Create | `BitbucketConfig`, `BitbucketToolset`, helpers, 10 tool classes, exceptions |
| `holmes/plugins/toolsets/__init__.py` | Modify | Import + register `BitbucketToolset` |
| `tests/plugins/toolsets/test_bitbucket.py` | Create | ~35 unit tests mirroring `test_pagerduty.py` |
| `frontend/server_frontend.py` | Modify | New `_test_bitbucket_instance_connection` helper + dispatcher branch |
| `tests/frontend/test_instances_api.py` | Modify | Append `TestBitbucketConnectionHelper` (3 tests) |
| `frontend/src/components/Instances.tsx` | Modify | Add `'bitbucket'` to `TOOLSET_TYPES`, add config block with repositories chip editor |
| `docs/data-sources/builtin-toolsets/bitbucket.md` | Create | User-facing docs |
| `docs/data-sources/builtin-toolsets/.nav.yml` | Modify | Insert alphabetical nav entry |

---

## Task 1: Package scaffold + `BitbucketConfig` + custom exceptions

**Files:**
- Create: `holmes/plugins/toolsets/bitbucket/__init__.py` (empty)
- Create: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Create: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Create `tests/plugins/toolsets/test_bitbucket.py`:

```python
"""Unit tests for the Bitbucket Cloud toolset."""

import pytest
from pydantic import ValidationError

from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import (
    BitbucketAuthError,
    BitbucketConfig,
    BitbucketForbiddenError,
    BitbucketRateLimitError,
    BitbucketToolset,
)


class TestBitbucketConfig:
    def test_minimum_config(self):
        cfg = BitbucketConfig(api_token="t", workspace="acme")
        assert cfg.api_token == "t"
        assert cfg.workspace == "acme"
        assert cfg.repositories is None
        assert cfg.default_limit == 25
        assert cfg.api_url == "https://api.bitbucket.org/2.0"

    def test_with_repositories_allowlist(self):
        cfg = BitbucketConfig(
            api_token="t",
            workspace="acme",
            repositories=["checkout-api", "inventory-db"],
        )
        assert cfg.repositories == ["checkout-api", "inventory-db"]

    def test_missing_workspace_raises(self):
        with pytest.raises(ValidationError):
            BitbucketConfig(api_token="t")

    def test_api_url_override(self):
        cfg = BitbucketConfig(api_token="t", workspace="acme", api_url="http://localhost:9000")
        assert cfg.api_url == "http://localhost:9000"


class TestBitbucketExceptionsExist:
    """Verify the three custom exception classes are importable RuntimeError subclasses."""

    def test_auth_error(self):
        assert issubclass(BitbucketAuthError, RuntimeError)

    def test_forbidden_error(self):
        assert issubclass(BitbucketForbiddenError, RuntimeError)

    def test_rate_limit_error(self):
        assert issubclass(BitbucketRateLimitError, RuntimeError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'holmes.plugins.toolsets.bitbucket'`.

- [ ] **Step 3: Create the package**

Create empty `holmes/plugins/toolsets/bitbucket/__init__.py`.

Create `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`:

```python
"""Bitbucket Cloud toolset for read-only repo, PR, commit, and file operations."""

import json
import logging
import re
from typing import Any, List, Optional, Tuple, Type

import requests
from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
)
from holmes.core.tools import ClassVar as ToolsClassVar
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0"


class BitbucketAuthError(RuntimeError):
    """Raised when Bitbucket returns 401."""


class BitbucketForbiddenError(RuntimeError):
    """Raised when Bitbucket returns 403."""


class BitbucketRateLimitError(RuntimeError):
    """Raised when Bitbucket returns 429."""


class BitbucketConfig(ToolsetConfig):
    """Configuration for Bitbucket Cloud API access."""

    api_token: str = Field(
        title="API Token",
        description="Bitbucket API token (Atlassian token with Bitbucket scopes).",
    )
    workspace: str = Field(
        title="Workspace",
        description="Bitbucket workspace slug (e.g. 'pdi-logistics').",
    )
    api_url: str = Field(
        default=BITBUCKET_API_BASE,
        title="API URL",
        description="Bitbucket API base URL. Override for on-prem forks or local mocks.",
    )
    default_limit: int = Field(
        default=25,
        title="Default Result Limit",
        description="Default page size for list endpoints. Capped at 100.",
    )
    repositories: Optional[List[str]] = Field(
        default=None,
        title="Repository Allowlist",
        description=(
            "Optional allowlist of repo slugs. When set, every tool call is restricted "
            "to these repos. Leave unset to allow all repos in the workspace."
        ),
    )


class BitbucketToolset(Toolset):
    """Bitbucket Cloud toolset for querying repos, pull requests, commits, and files."""

    config_classes: ToolsClassVar[list[Type[BitbucketConfig]]] = [BitbucketConfig]

    bb_config: Optional[BitbucketConfig] = None

    def __init__(self):
        super().__init__(
            name="bitbucket",
            description="Read-only access to Bitbucket Cloud: repositories, pull requests, commits, and file contents.",
            docs_url="https://developer.atlassian.com/cloud/bitbucket/rest/",
            icon_url="https://cdn.simpleicons.org/bitbucket/0052CC",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],  # Tools added in later tasks.
            tags=[ToolsetTag.CORE],
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "Missing Bitbucket configuration. Provide api_token and workspace."
        try:
            self.bb_config = BitbucketConfig(**config)
            return True, ""   # _health_check added in Task 2.
        except Exception as e:
            return False, f"Failed to configure Bitbucket toolset: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 7 tests PASS (4 config + 3 exception).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/ tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): scaffold package, config model, custom exceptions"
```

---

## Task 2: `get()` HTTP wrapper + `_health_check`

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
from unittest.mock import MagicMock, patch


def _mock_resp(status_code: int, json_body=None, text="", headers=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body or {}
    m.text = text
    m.headers = headers or {}
    m.raise_for_status = MagicMock()
    if status_code >= 400:
        m.raise_for_status.side_effect = requests.HTTPError(response=m)
    return m


class TestHealthCheck:
    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_200(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"slug": "acme"})
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "t", "workspace": "acme"})
        assert ok is True
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer t"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_401(self, mock_get):
        mock_get.return_value = _mock_resp(401, text="Unauthorized")
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "bad", "workspace": "acme"})
        assert ok is False
        assert "rejected" in msg.lower() or "401" in msg

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_403(self, mock_get):
        mock_get.return_value = _mock_resp(403, text="Forbidden")
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "t", "workspace": "acme"})
        assert ok is False
        assert "no access" in msg.lower() or "403" in msg

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_network_exception(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("connrefused")
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "t", "workspace": "acme"})
        assert ok is False
        assert "failed" in msg.lower()


class TestGetHTTPWrapper:
    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_401_raises_auth_error(self, mock_get):
        mock_get.return_value = _mock_resp(401)
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(BitbucketAuthError):
            ts.get("/repositories/acme")

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_403_raises_forbidden_error(self, mock_get):
        mock_get.return_value = _mock_resp(403)
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(BitbucketForbiddenError):
            ts.get("/repositories/acme")

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_429_raises_rate_limit_with_retry_after(self, mock_get):
        mock_get.return_value = _mock_resp(429, headers={"Retry-After": "60"})
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(BitbucketRateLimitError) as exc_info:
            ts.get("/repositories/acme")
        assert "60" in str(exc_info.value)

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_404_bubbles_httperror(self, mock_get):
        mock_get.return_value = _mock_resp(404)
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(requests.HTTPError):
            ts.get("/repositories/acme/nope")

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_200_returns_json(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"slug": "x", "values": []})
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        result = ts.get("/repositories/acme", params={"pagelen": 10})
        assert result == {"slug": "x", "values": []}
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"pagelen": 10}
        assert kwargs["headers"]["Authorization"] == "Bearer t"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: FAIL — `get()` and `_health_check` not implemented.

- [ ] **Step 3: Implement `get()` and `_health_check`**

In `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`, add these methods to `BitbucketToolset`:

```python
    def _headers(self) -> dict:
        assert self.bb_config is not None
        return {
            "Authorization": f"Bearer {self.bb_config.api_token}",
            "Accept": "application/json",
        }

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET a Bitbucket API path and return parsed JSON.

        Maps 401/403/429 to friendly custom exceptions.
        404 and other HTTPErrors bubble for caller-specific handling.
        """
        assert self.bb_config is not None
        url = f"{self.bb_config.api_url}{path}"
        resp = requests.get(
            url, headers=self._headers(), params=params or {}, timeout=30
        )
        if resp.status_code == 401:
            raise BitbucketAuthError(
                "Bitbucket API token rejected (401). Check the secret configured for this instance."
            )
        if resp.status_code == 403:
            raise BitbucketForbiddenError(
                f"Token has no access to workspace '{self.bb_config.workspace}' (or to this resource). "
                "Verify the token's Repository scopes."
            )
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "unknown")
            raise BitbucketRateLimitError(
                f"Bitbucket API rate limit exceeded (429). Retry-After: {retry_after}"
            )
        resp.raise_for_status()
        return resp.json()

    def _health_check(self) -> Tuple[bool, str]:
        assert self.bb_config is not None
        try:
            self.get(f"/workspaces/{self.bb_config.workspace}")
            return True, ""
        except BitbucketAuthError as e:
            return False, str(e)
        except BitbucketForbiddenError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Bitbucket health check failed: {e}"
```

Wire `_health_check` into `prerequisites_callable` — replace the existing method body:

```python
    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "Missing Bitbucket configuration. Provide api_token and workspace."
        try:
            self.bb_config = BitbucketConfig(**config)
        except Exception as e:
            return False, f"Failed to configure Bitbucket toolset: {e}"
        return self._health_check()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 16 tests PASS (7 previous + 4 health + 5 get-wrapper).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): get() wrapper with friendly 401/403/429 errors + health check"
```

---

## Task 3: Helpers — `_check_repo_in_scope`, `_truncate`, input validation

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
from holmes.core.tools import StructuredToolResultStatus


class TestCheckRepoInScope:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    def test_no_allowlist_any_repo_allowed(self):
        ts = self._toolset()
        assert ts._check_repo_in_scope("anything", {}) is None

    def test_allowlist_in_scope(self):
        ts = self._toolset(repositories=["a", "b"])
        assert ts._check_repo_in_scope("a", {}) is None

    def test_allowlist_out_of_scope_returns_error(self):
        ts = self._toolset(repositories=["a"])
        result = ts._check_repo_in_scope("b", {"repo_slug": "b"})
        assert result is not None
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        assert "['a']" in result.error

    def test_allowlist_case_insensitive(self):
        ts = self._toolset(repositories=["Checkout-API"])
        assert ts._check_repo_in_scope("checkout-api", {}) is None


class TestTruncate:
    def test_under_limit_unchanged(self):
        ts = BitbucketToolset()
        result = ts._truncate("small", max_bytes=100)
        assert result == "small"

    def test_byte_based_trim(self):
        ts = BitbucketToolset()
        payload = "a" * 500 + "\n" + "b" * 500
        result = ts._truncate(payload, max_bytes=600)
        assert "truncated" in result
        assert len(result.encode("utf-8")) <= 800  # rough bound: marker adds overhead

    def test_line_based_trim(self):
        ts = BitbucketToolset()
        payload = "\n".join(f"line {i}" for i in range(5000))
        result = ts._truncate(payload, max_bytes=0, line_mode=True, max_lines=2000)
        assert "truncated" in result
        assert result.count("\n") <= 2001  # 2000 lines + marker line


class TestInputValidation:
    def test_valid_repo_slug(self):
        ts = BitbucketToolset()
        assert ts._validate_repo_slug("checkout-api") is True
        assert ts._validate_repo_slug("a.b_c-d123") is True

    def test_invalid_repo_slug_rejects_path_traversal(self):
        ts = BitbucketToolset()
        assert ts._validate_repo_slug("foo/../bar") is False
        assert ts._validate_repo_slug("foo/bar") is False
        assert ts._validate_repo_slug("UPPER") is False

    def test_valid_ref(self):
        ts = BitbucketToolset()
        assert ts._validate_ref("main") is True
        assert ts._validate_ref("feature/new-thing") is True
        assert ts._validate_ref("v1.2.3") is True

    def test_invalid_ref_rejects_double_dot(self):
        ts = BitbucketToolset()
        assert ts._validate_ref("foo/../etc") is False
        assert ts._validate_ref("..") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestCheckRepoInScope tests/plugins/toolsets/test_bitbucket.py::TestTruncate tests/plugins/toolsets/test_bitbucket.py::TestInputValidation -v --no-cov`
Expected: FAIL — helpers not implemented.

- [ ] **Step 3: Implement the helpers**

Add to `BitbucketToolset` in `toolset_bitbucket.py`:

```python
    _REPO_SLUG_RE = re.compile(r"^[a-z0-9._-]+$")
    _REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

    def _check_repo_in_scope(
        self, repo_slug: str, params: dict
    ) -> Optional[StructuredToolResult]:
        """Return an ERROR result if the repo is outside the instance's scope; else None.

        When the instance has a `repositories` allowlist, every repo-specific tool
        call must match one of its entries (case-insensitive). Missing allowlist =
        all repos in the workspace are allowed.
        """
        assert self.bb_config is not None
        allowed = self.bb_config.repositories
        if not allowed:
            return None
        normalized = repo_slug.strip().lower()
        allowed_lower = [r.strip().lower() for r in allowed]
        if normalized in allowed_lower:
            return None
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=(
                f"Repository '{repo_slug}' is not in this project's scope "
                f"(allowed: {allowed})"
            ),
            params=params,
        )

    @staticmethod
    def _truncate(
        text: str,
        max_bytes: int,
        *,
        line_mode: bool = False,
        max_lines: int = 2000,
    ) -> str:
        """Trim `text` with a trailing marker when it exceeds the configured cap.

        Byte mode: trim on a line boundary when possible.
        Line mode: cap line count at `max_lines`.
        """
        if line_mode:
            lines = text.splitlines()
            if len(lines) <= max_lines:
                return text
            kept = lines[:max_lines]
            dropped = len(lines) - max_lines
            return "\n".join(kept) + f"\n[... truncated {dropped} lines ...]"
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        trimmed = encoded[:max_bytes].decode("utf-8", errors="ignore")
        # Snap back to the last newline for readability.
        last_nl = trimmed.rfind("\n")
        if last_nl > max_bytes // 2:
            trimmed = trimmed[:last_nl]
        dropped_bytes = len(encoded) - len(trimmed.encode("utf-8"))
        return f"{trimmed}\n[... truncated {dropped_bytes} bytes ...]"

    @classmethod
    def _validate_repo_slug(cls, slug: str) -> bool:
        return bool(slug) and bool(cls._REPO_SLUG_RE.match(slug))

    @classmethod
    def _validate_ref(cls, ref: str) -> bool:
        if not ref or ".." in ref:
            return False
        return bool(cls._REF_RE.match(ref))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 27 tests PASS (16 previous + 4 scope + 3 truncate + 4 validation).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): scope guard, truncation, and input-validation helpers"
```

---

## Task 4: Repo tools — `ListBitbucketRepositories`, `GetBitbucketRepository`

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
from tests.conftest import create_mock_tool_invoke_context


class TestRepoTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_repositories_hits_correct_url(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": [{"slug": "checkout-api"}]})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_repositories")
        result = tool._invoke({}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme" in args[0]

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_repositories_caps_pagelen_at_100(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_repositories")
        tool._invoke({"limit": 500}, create_mock_tool_invoke_context())
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["pagelen"] == 100

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_repository_in_scope(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"slug": "checkout-api", "mainbranch": {"name": "main"}})
        ts = self._toolset(repositories=["checkout-api"])
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "checkout-api"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.SUCCESS

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_repository_out_of_scope_blocks_before_api_call(self, mock_get):
        ts = self._toolset(repositories=["a"])
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "b"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        assert mock_get.call_count == 0

    def test_get_repository_invalid_slug_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "foo/../bar"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid repo_slug" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestRepoTools -v --no-cov`
Expected: FAIL — tool classes not yet implemented.

- [ ] **Step 3: Implement the tools**

Append to `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py` (below `BitbucketToolset`):

```python
class _BaseBitbucketTool(Tool):
    toolset: "BitbucketToolset"

    def _err(self, params: dict, msg: str) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR, error=msg, params=params
        )

    def _ok(self, params: dict, data: Any, url: str = "") -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=json.dumps(data, indent=2) if not isinstance(data, str) else data,
            params=params,
            url=url,
        )

    def _capped_limit(self, user_limit: Any) -> int:
        try:
            n = int(user_limit) if user_limit else self.toolset.bb_config.default_limit
        except (TypeError, ValueError):
            n = self.toolset.bb_config.default_limit
        return max(1, min(n, 100))


class ListBitbucketRepositories(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="list_bitbucket_repositories",
            description="[bitbucket toolset] List repositories in the instance's workspace",
            parameters={
                "limit": ToolParameter(
                    description="Max repos to return (default 25, max 100)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List repos"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        try:
            query = {"pagelen": self._capped_limit(params.get("limit"))}
            path = f"/repositories/{self.toolset.bb_config.workspace}"
            data = self.toolset.get(path, params=query)
            return self._ok(params, data)
        except Exception as e:
            logging.exception("Failed to list Bitbucket repositories")
            return self._err(params, str(e))


class GetBitbucketRepository(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="get_bitbucket_repository",
            description="[bitbucket toolset] Get details for a specific repository",
            parameters={
                "repo_slug": ToolParameter(
                    description="Repository slug (e.g. 'checkout-api')",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get repo {params.get('repo_slug', '')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo_slug = params.get("repo_slug", "")
        if not self.toolset._validate_repo_slug(repo_slug):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        scope_err = self.toolset._check_repo_in_scope(repo_slug, params)
        if scope_err is not None:
            return scope_err
        try:
            data = self.toolset.get(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo_slug}"
            )
            return self._ok(params, data, url=data.get("links", {}).get("html", {}).get("href", ""))
        except Exception as e:
            logging.exception("Failed to get Bitbucket repository")
            return self._err(params, str(e))
```

Register both tools in `BitbucketToolset.__init__` by replacing `tools=[]` with:

```python
            tools=[
                ListBitbucketRepositories(toolset=self),
                GetBitbucketRepository(toolset=self),
            ],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 32 tests PASS (27 previous + 5 repo tools).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): repo tools (list, get) with scope guard + input validation"
```

---

## Task 5: Pull request tools — list, get, comments

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
class TestPullRequestTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_default_state_open(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        tool._invoke({"repo_slug": "x"}, create_mock_tool_invoke_context())
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["state"] == "OPEN"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_state_override(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        tool._invoke(
            {"repo_slug": "x", "state": "MERGED"},
            create_mock_tool_invoke_context(),
        )
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["state"] == "MERGED"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_out_of_scope_blocks(self, mock_get):
        ts = self._toolset(repositories=["a"])
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        result = tool._invoke({"repo_slug": "b"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert mock_get.call_count == 0

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_pr(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"id": 42, "title": "Fix"})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/pullrequests/42" in args[0]

    def test_get_pr_missing_id(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "x"}, create_mock_tool_invoke_context()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "pull_request_id is required" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_pr_comments(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": [{"content": {"raw": "nit"}}]})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_request_comments")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/pullrequests/42/comments" in args[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestPullRequestTools -v --no-cov`
Expected: FAIL — tool classes not yet implemented.

- [ ] **Step 3: Implement PR tools**

Append to `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`:

```python
class ListBitbucketPullRequests(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="list_bitbucket_pull_requests",
            description="[bitbucket toolset] List pull requests in a repository (default state=OPEN)",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "state": ToolParameter(
                    description="PR state: OPEN, MERGED, DECLINED, SUPERSEDED (default: OPEN)",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(description="Max results (default 25, max 100)", type="integer", required=False),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List PRs in {params.get('repo_slug', '')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        state = params.get("state", "OPEN")
        try:
            data = self.toolset.get(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/pullrequests",
                params={"state": state, "pagelen": self._capped_limit(params.get("limit"))},
            )
            return self._ok(params, data)
        except Exception as e:
            logging.exception("Failed to list Bitbucket PRs")
            return self._err(params, str(e))


class GetBitbucketPullRequest(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="get_bitbucket_pull_request",
            description="[bitbucket toolset] Get full details for a specific pull request",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "pull_request_id": ToolParameter(description="PR id (integer as string)", type="string", required=True),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get PR {params.get('repo_slug')}#{params.get('pull_request_id')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        pr_id = str(params.get("pull_request_id", "") or "").strip()
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not pr_id:
            return self._err(params, "pull_request_id is required")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        try:
            data = self.toolset.get(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/pullrequests/{pr_id}"
            )
            return self._ok(params, data, url=data.get("links", {}).get("html", {}).get("href", ""))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._err(params, f"Pull request {repo}#{pr_id} not found")
            return self._err(params, str(e))
        except Exception as e:
            logging.exception("Failed to get Bitbucket PR")
            return self._err(params, str(e))


class ListBitbucketPullRequestComments(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="list_bitbucket_pull_request_comments",
            description="[bitbucket toolset] List comments on a specific pull request",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "pull_request_id": ToolParameter(description="PR id", type="string", required=True),
                "limit": ToolParameter(description="Max comments (default 25, max 100)", type="integer", required=False),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: PR comments {params.get('repo_slug')}#{params.get('pull_request_id')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        pr_id = str(params.get("pull_request_id", "") or "").strip()
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not pr_id:
            return self._err(params, "pull_request_id is required")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        try:
            data = self.toolset.get(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/pullrequests/{pr_id}/comments",
                params={"pagelen": self._capped_limit(params.get("limit"))},
            )
            return self._ok(params, data)
        except Exception as e:
            logging.exception("Failed to list Bitbucket PR comments")
            return self._err(params, str(e))
```

Add the three tools to `BitbucketToolset.__init__.tools`:

```python
            tools=[
                ListBitbucketRepositories(toolset=self),
                GetBitbucketRepository(toolset=self),
                ListBitbucketPullRequests(toolset=self),
                GetBitbucketPullRequest(toolset=self),
                ListBitbucketPullRequestComments(toolset=self),
            ],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 38 tests PASS (32 previous + 6 PR tools).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): PR tools (list, get, comments) with scope guard"
```

---

## Task 6: Diff tools with truncation — PR diff, commit diff

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
class TestDiffTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_pr_diff_small_not_truncated(self, mock_get):
        mock_get.return_value = _mock_resp(200, text="diff --git a/x b/x\n+1 line")
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request_diff")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" not in result.data

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_pr_diff_large_truncated(self, mock_get):
        # 500 KB payload, default cap is 200 KB.
        big = "a" * 500_000
        mock_get.return_value = _mock_resp(200, text=big)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request_diff")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" in result.data
        assert len(result.data.encode("utf-8")) < 250_000

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_commit_diff_hits_correct_url(self, mock_get):
        mock_get.return_value = _mock_resp(200, text="diff ...")
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit_diff")
        result = tool._invoke(
            {"repo_slug": "x", "commit_sha": "abc123"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/diff/abc123" in args[0]

    def test_commit_diff_missing_sha(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit_diff")
        result = tool._invoke({"repo_slug": "x"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "commit_sha is required" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestDiffTools -v --no-cov`
Expected: FAIL.

- [ ] **Step 3: Implement diff tools**

The diff endpoints return `text/plain` rather than JSON. Add a helper for raw text GETs to `BitbucketToolset`:

```python
    def get_text(self, path: str, params: Optional[dict] = None) -> str:
        assert self.bb_config is not None
        url = f"{self.bb_config.api_url}{path}"
        resp = requests.get(url, headers=self._headers(), params=params or {}, timeout=60)
        if resp.status_code == 401:
            raise BitbucketAuthError(
                "Bitbucket API token rejected (401). Check the secret configured for this instance."
            )
        if resp.status_code == 403:
            raise BitbucketForbiddenError(
                f"Token has no access to workspace '{self.bb_config.workspace}' (or to this resource). "
                "Verify the token's Repository scopes."
            )
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "unknown")
            raise BitbucketRateLimitError(
                f"Bitbucket API rate limit exceeded (429). Retry-After: {retry_after}"
            )
        resp.raise_for_status()
        return resp.text
```

Append the two tool classes:

```python
PR_DIFF_MAX_BYTES = 200_000
COMMIT_DIFF_MAX_BYTES = 200_000


class GetBitbucketPullRequestDiff(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="get_bitbucket_pull_request_diff",
            description="[bitbucket toolset] Get the unified diff of a pull request (truncated at ~200 KB by default)",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "pull_request_id": ToolParameter(description="PR id", type="string", required=True),
                "max_bytes": ToolParameter(
                    description="Max diff bytes to return (default 200000)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: PR diff {params.get('repo_slug')}#{params.get('pull_request_id')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        pr_id = str(params.get("pull_request_id", "") or "").strip()
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not pr_id:
            return self._err(params, "pull_request_id is required")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        max_bytes = int(params.get("max_bytes") or PR_DIFF_MAX_BYTES)
        try:
            raw = self.toolset.get_text(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/pullrequests/{pr_id}/diff"
            )
            return self._ok(params, self.toolset._truncate(raw, max_bytes=max_bytes))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._err(params, f"Pull request {repo}#{pr_id} not found")
            return self._err(params, str(e))
        except Exception as e:
            logging.exception("Failed to fetch PR diff")
            return self._err(params, str(e))


class GetBitbucketCommitDiff(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="get_bitbucket_commit_diff",
            description="[bitbucket toolset] Get the unified diff of a specific commit (truncated at ~200 KB by default)",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "commit_sha": ToolParameter(description="Commit SHA or ref", type="string", required=True),
                "max_bytes": ToolParameter(
                    description="Max diff bytes (default 200000)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Commit diff {params.get('repo_slug')}@{params.get('commit_sha', '')[:8]}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        sha = str(params.get("commit_sha", "") or "").strip()
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not sha:
            return self._err(params, "commit_sha is required")
        if not self.toolset._validate_ref(sha):
            return self._err(params, "Invalid commit_sha")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        max_bytes = int(params.get("max_bytes") or COMMIT_DIFF_MAX_BYTES)
        try:
            raw = self.toolset.get_text(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/diff/{sha}"
            )
            return self._ok(params, self.toolset._truncate(raw, max_bytes=max_bytes))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._err(params, f"Commit {repo}@{sha} not found")
            return self._err(params, str(e))
        except Exception as e:
            logging.exception("Failed to fetch commit diff")
            return self._err(params, str(e))
```

Add both to `BitbucketToolset.__init__.tools`:

```python
                GetBitbucketPullRequestDiff(toolset=self),
                GetBitbucketCommitDiff(toolset=self),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 42 tests PASS (38 previous + 4 diff tools).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): PR and commit diff tools with 200KB truncation"
```

---

## Task 7: Commit tools — list + get

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
class TestCommitTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_commits_default_branch(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_commits")
        result = tool._invoke(
            {"repo_slug": "x", "branch": "main"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/commits/main" in args[0]

    def test_list_commits_invalid_branch_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_commits")
        result = tool._invoke(
            {"repo_slug": "x", "branch": "main/../evil"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_commit(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"hash": "abc123", "message": "Fix"})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit")
        result = tool._invoke(
            {"repo_slug": "x", "commit_sha": "abc123"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/commit/abc123" in args[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestCommitTools -v --no-cov`
Expected: FAIL.

- [ ] **Step 3: Implement commit tools**

Append to `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`:

```python
class ListBitbucketCommits(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="list_bitbucket_commits",
            description="[bitbucket toolset] List commits on a branch (or commit ref)",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "branch": ToolParameter(description="Branch name or commit ref", type="string", required=True),
                "limit": ToolParameter(description="Max commits (default 25, max 100)", type="integer", required=False),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Commits {params.get('repo_slug')}@{params.get('branch')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        branch = params.get("branch", "")
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not self.toolset._validate_ref(branch):
            return self._err(params, "Invalid branch")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        try:
            data = self.toolset.get(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/commits/{branch}",
                params={"pagelen": self._capped_limit(params.get("limit"))},
            )
            return self._ok(params, data)
        except Exception as e:
            logging.exception("Failed to list Bitbucket commits")
            return self._err(params, str(e))


class GetBitbucketCommit(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="get_bitbucket_commit",
            description="[bitbucket toolset] Get details for a specific commit",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "commit_sha": ToolParameter(description="Commit SHA", type="string", required=True),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Commit {params.get('repo_slug')}@{params.get('commit_sha', '')[:8]}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        sha = str(params.get("commit_sha", "") or "").strip()
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not sha:
            return self._err(params, "commit_sha is required")
        if not self.toolset._validate_ref(sha):
            return self._err(params, "Invalid commit_sha")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        try:
            data = self.toolset.get(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/commit/{sha}"
            )
            return self._ok(params, data, url=data.get("links", {}).get("html", {}).get("href", ""))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._err(params, f"Commit {repo}@{sha} not found")
            return self._err(params, str(e))
        except Exception as e:
            logging.exception("Failed to get Bitbucket commit")
            return self._err(params, str(e))
```

Register in `BitbucketToolset.__init__.tools`:

```python
                ListBitbucketCommits(toolset=self),
                GetBitbucketCommit(toolset=self),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 45 tests PASS (42 previous + 3 commit tools).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): commit tools (list, get) with ref validation"
```

---

## Task 8: File contents tool with line truncation

**Files:**
- Modify: `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
class TestFileContentsTool:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_file_contents_small(self, mock_get):
        mock_get.return_value = _mock_resp(200, text="print('hello')\n")
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main", "path": "src/app.py"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" not in result.data
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/src/main/src/app.py" in args[0]

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_file_contents_large_truncated_at_2000_lines(self, mock_get):
        big = "\n".join(f"line {i}" for i in range(5000))
        mock_get.return_value = _mock_resp(200, text=big)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main", "path": "big.txt"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" in result.data
        # Expect 2000 kept lines + 1 marker line
        assert result.data.count("\n") <= 2001

    def test_file_contents_invalid_ref_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main/../evil", "path": "x"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid ref" in result.error

    def test_file_contents_missing_path(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "path is required" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestFileContentsTool -v --no-cov`
Expected: FAIL.

- [ ] **Step 3: Implement the tool**

Append to `holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py`:

```python
FILE_CONTENTS_MAX_LINES = 2000


class GetBitbucketFileContents(_BaseBitbucketTool):
    def __init__(self, toolset: "BitbucketToolset"):
        super().__init__(
            name="get_bitbucket_file_contents",
            description="[bitbucket toolset] Get the contents of a file at a specific ref (branch/commit/tag), truncated at 2000 lines by default",
            parameters={
                "repo_slug": ToolParameter(description="Repo slug", type="string", required=True),
                "ref": ToolParameter(description="Branch, commit SHA, or tag", type="string", required=True),
                "path": ToolParameter(description="File path inside the repo (e.g. 'src/app.py')", type="string", required=True),
                "max_lines": ToolParameter(
                    description="Max lines to return (default 2000)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: File {params.get('repo_slug')}:{params.get('path')}@{params.get('ref')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.bb_config:
            return self._err(params, "Bitbucket not configured")
        repo = params.get("repo_slug", "")
        ref = params.get("ref", "")
        path = params.get("path", "") or ""
        if not self.toolset._validate_repo_slug(repo):
            return self._err(params, "Invalid repo_slug: must match [a-z0-9._-]+")
        if not self.toolset._validate_ref(ref):
            return self._err(params, "Invalid ref")
        if not path.strip():
            return self._err(params, "path is required")
        if ".." in path:
            return self._err(params, "Invalid path: must not contain '..'")
        scope_err = self.toolset._check_repo_in_scope(repo, params)
        if scope_err is not None:
            return scope_err
        max_lines = int(params.get("max_lines") or FILE_CONTENTS_MAX_LINES)
        try:
            raw = self.toolset.get_text(
                f"/repositories/{self.toolset.bb_config.workspace}/{repo}/src/{ref}/{path}"
            )
            truncated = self.toolset._truncate(raw, max_bytes=0, line_mode=True, max_lines=max_lines)
            return self._ok(params, truncated)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._err(params, f"File not found: {repo}:{path}@{ref}")
            return self._err(params, str(e))
        except Exception as e:
            logging.exception("Failed to fetch Bitbucket file contents")
            return self._err(params, str(e))
```

Add to `BitbucketToolset.__init__.tools`:

```python
                GetBitbucketFileContents(toolset=self),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 49 tests PASS (45 previous + 4 file tool).

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/bitbucket/toolset_bitbucket.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): file contents tool with 2000-line truncation"
```

---

## Task 9: Register `bitbucket` in the toolset registry

**Files:**
- Modify: `holmes/plugins/toolsets/__init__.py`
- Modify: `tests/plugins/toolsets/test_bitbucket.py`

- [ ] **Step 1: Write failing test**

Append to `tests/plugins/toolsets/test_bitbucket.py`:

```python
class TestFactoryRegistration:
    def test_bitbucket_in_python_toolset_factories(self):
        from holmes.plugins.toolsets import PYTHON_TOOLSET_FACTORIES

        assert "bitbucket" in PYTHON_TOOLSET_FACTORIES
        assert PYTHON_TOOLSET_FACTORIES["bitbucket"] is BitbucketToolset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py::TestFactoryRegistration -v --no-cov`
Expected: FAIL — `"bitbucket"` not in `PYTHON_TOOLSET_FACTORIES`.

- [ ] **Step 3: Register**

In `holmes/plugins/toolsets/__init__.py`:

1. Add import alongside the other toolset imports (near line 55, where `PagerDutyToolset` is imported):

```python
from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import BitbucketToolset
```

2. Add to the global toolsets list (near line 126 where `PagerDutyToolset()` is appended):

```python
        BitbucketToolset(),
```

3. Add to `PYTHON_TOOLSET_FACTORIES` (near line 280 where `"pagerduty": PagerDutyToolset` is registered):

```python
    "bitbucket": BitbucketToolset,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/plugins/toolsets/test_bitbucket.py -v --no-cov`
Expected: 50 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add holmes/plugins/toolsets/__init__.py tests/plugins/toolsets/test_bitbucket.py
git commit -s --no-verify -m "feat(bitbucket): register in PYTHON_TOOLSET_FACTORIES for per-project instances"
```

---

## Task 10: Test Connection endpoint support

**Files:**
- Modify: `frontend/server_frontend.py`
- Modify: `tests/frontend/test_instances_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/frontend/test_instances_api.py`:

```python
class TestBitbucketConnectionHelper:
    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    @patch("projects._fetch_secret")
    def test_bitbucket_connection_success_via_secret_arn(
        self, mock_secret, mock_get
    ):
        from server_frontend import _test_bitbucket_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_bb1",
            type="bitbucket",
            name="bb-test",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:bb-test",
        )
        mock_secret.return_value = {"api_token": "t", "workspace": "acme"}

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"slug": "acme"}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.run(_test_bitbucket_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    @patch("projects._fetch_secret")
    def test_bitbucket_connection_403_returns_clear_error(
        self, mock_secret, mock_get
    ):
        from server_frontend import _test_bitbucket_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_bb2",
            type="bitbucket",
            name="bb-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:bb-bad",
        )
        mock_secret.return_value = {"api_token": "t", "workspace": "acme"}

        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Forbidden"
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.run(_test_bitbucket_instance_connection(store, inst))
        assert body["ok"] is False
        assert "no access" in body["error"].lower() or "403" in body["error"]

    def test_bitbucket_no_credential_source(self):
        from server_frontend import _test_bitbucket_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(id="inst_bb3", type="bitbucket", name="bb-empty")
        store = MagicMock()
        body = asyncio.run(_test_bitbucket_instance_connection(store, inst))
        assert body["ok"] is False
        assert "credential source" in body["error"].lower() or "secret_arn" in body["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/frontend/test_instances_api.py::TestBitbucketConnectionHelper -v --no-cov`
Expected: FAIL — `_test_bitbucket_instance_connection` not yet defined.

- [ ] **Step 3: Implement the helper**

In `frontend/server_frontend.py`, add a new module-level async helper alongside the existing AWS / PagerDuty / MCP helpers (around line 489, above `mount_frontend`):

```python
async def _test_bitbucket_instance_connection(store, inst):
    """Test a Bitbucket instance by fetching the workspace via prerequisites_callable.
    Returns dict payload for JSONResponse.
    """
    from projects import _fetch_secret  # noqa: PLC0415
    from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import (  # noqa: PLC0415
        BitbucketToolset,
    )

    if not inst.secret_arn:
        return {
            "ok": False,
            "status": "error",
            "error": "Bitbucket instance has no credential source (secret_arn required)",
        }
    try:
        creds = _fetch_secret(inst.secret_arn)
    except Exception as e:
        return {"ok": False, "status": "error", "error": f"Failed to fetch secret: {e}"}
    if "api_token" not in creds or "workspace" not in creds:
        return {
            "ok": False,
            "status": "error",
            "error": "Secret must contain `api_token` and `workspace` fields",
        }

    cfg = {**creds, **(inst.config or {})}
    ts = BitbucketToolset()
    ok, msg = ts.prerequisites_callable(cfg)
    if ok:
        return {"ok": True, "status": "success"}
    # Defensively strip the token from the error before returning.
    token = creds.get("api_token", "")
    if msg and token and token in msg:
        msg = msg.replace(token, "<redacted>")
    return {"ok": False, "status": "error", "error": msg}
```

Extend the dispatcher in the `test_instance_connection` route (around line 1440) — add a new branch between `pagerduty` and the `_MCP_TOOLSET_TYPES` check:

```python
            if inst.type == "bitbucket":
                body = await _test_bitbucket_instance_connection(store, inst)
                return JSONResponse(body)
```

Place it anywhere before the `raise HTTPException(400, ...)` fallback.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/frontend/test_instances_api.py -v --no-cov`
Expected: 13 tests PASS (10 existing + 3 new Bitbucket).

- [ ] **Step 5: Commit**

```bash
git add frontend/server_frontend.py tests/frontend/test_instances_api.py
git commit -s --no-verify -m "feat(api): extend test-connection to handle bitbucket instances"
```

---

## Task 11: Frontend UI — bitbucket instance type + config block

**Files:**
- Modify: `frontend/src/components/Instances.tsx`

- [ ] **Step 1: Add `bitbucket` to `TOOLSET_TYPES`**

Near the top of `frontend/src/components/Instances.tsx`, find the `TOOLSET_TYPES` array and add `'bitbucket'`:

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
  'bitbucket',
]
```

Do **not** add `'bitbucket'` to `MCP_TYPES` — it's a Python toolset, not MCP.

- [ ] **Step 2: Add state hooks in `InstanceFormDialog`**

Near the existing `pdServiceIds` / `pdTeamIds` state hooks (around line 120-130), add:

```typescript
type BitbucketInstanceConfig = {
  repositories?: string[]
}

const [bbRepositories, setBbRepositories] = useState<string[]>(
  (instance?.config as BitbucketInstanceConfig | null | undefined)?.repositories ?? []
)

const isBitbucket = type === 'bitbucket'
```

(You may place `type BitbucketInstanceConfig` near the top of the file next to `PagerDutyInstanceConfig` for consistency.)

- [ ] **Step 3: Include Bitbucket config in save payload**

Find the object passed to `api.createInstance` / `api.updateInstance` (near line 150, where PagerDuty config is serialized). Extend the `config` field so Bitbucket's `repositories` list is written:

```typescript
config: isPagerDuty
  ? (pdServiceIds.length > 0 || pdTeamIds.length > 0
      ? {
          ...(pdServiceIds.length > 0 ? { service_ids: pdServiceIds } : {}),
          ...(pdTeamIds.length > 0 ? { team_ids: pdTeamIds } : {}),
        }
      : null)
  : isBitbucket
  ? (bbRepositories.length > 0 ? { repositories: bbRepositories } : null)
  : (instance?.config ?? null),
```

- [ ] **Step 4: Render the Bitbucket config block**

Add a block after the PagerDuty block (near line 475-549). It uses the existing `ChipListEditor` component:

```tsx
{isBitbucket && (
  <div className="space-y-4 p-4 bg-gray-50 rounded-lg border border-pdi-cool-gray">
    <p className="text-xs font-medium text-pdi-slate uppercase tracking-wider">
      Bitbucket Connection
    </p>
    <p className="text-xs text-pdi-slate">
      Store a Secrets Manager secret with{" "}
      <span className="font-mono">{"{\"api_token\": \"...\", \"workspace\": \"...\"}"}</span>{" "}
      and paste its ARN above. Optionally restrict this instance to a subset of repos.
    </p>

    <ChipListEditor
      label="Repository Allowlist (optional)"
      placeholder="e.g. checkout-api"
      values={bbRepositories}
      onChange={setBbRepositories}
    />

    {/* Connection status */}
    {connectionStatus && (
      <div className={`flex items-start gap-2 text-xs rounded-md px-3 py-2 ${
        connectionStatus === 'success'
          ? 'bg-pdi-grass/10 text-pdi-grass border border-pdi-grass/20'
          : 'bg-pdi-orange/10 text-pdi-orange border border-pdi-orange/20'
      }`}>
        <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
          connectionStatus === 'success' ? 'bg-pdi-grass' : 'bg-pdi-orange'
        }`} />
        <div>
          <span className="font-medium">
            {connectionStatus === 'success' ? 'Connected' : 'Connection failed'}
          </span>
          {connectionError && (
            <p className="mt-0.5 text-[11px] opacity-80 break-all">{connectionError}</p>
          )}
        </div>
      </div>
    )}

    {/* Test Connection button */}
    {instance && instance.secret_arn && (
      <button
        type="button"
        onClick={handleTestConnection}
        disabled={testing}
        className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-pdi-sky border border-pdi-sky/30 rounded-lg hover:bg-pdi-sky/5 transition-colors disabled:opacity-50"
      >
        {testing ? (
          <>
            <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Testing…
          </>
        ) : (
          <>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            Test Connection
          </>
        )}
      </button>
    )}
  </div>
)}
```

- [ ] **Step 5: Verify the frontend builds**

Run:

```bash
cd frontend && npm run build
```

Expected: `✓ built in <time>s`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Instances.tsx
git commit -s --no-verify -m "feat(ui): add bitbucket instance type with repository allowlist editor"
```

---

## Task 12: Docs + nav + regression

**Files:**
- Create: `docs/data-sources/builtin-toolsets/bitbucket.md`
- Modify: `docs/data-sources/builtin-toolsets/.nav.yml`

- [ ] **Step 1: Create the docs page**

Create `docs/data-sources/builtin-toolsets/bitbucket.md`:

```markdown
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
```

- [ ] **Step 2: Add nav entry**

In `docs/data-sources/builtin-toolsets/.nav.yml`, insert `- Bitbucket: bitbucket.md` alphabetically between `Bash` and `Cilium`:

```yaml
  - Bash: bash.md
  - Bitbucket: bitbucket.md
  - Cilium: cilium.md
```

- [ ] **Step 3: Final regression check**

Run:

```bash
poetry run pytest tests/plugins/toolsets/test_pagerduty.py tests/plugins/toolsets/test_bitbucket.py tests/frontend/ -v --no-cov
```

Expected: 28 (pagerduty) + 50 (bitbucket) + 13 (frontend, 10 existing + 3 new) = **91 tests passing**.

- [ ] **Step 4: Frontend build sanity**

Run: `cd frontend && npm run build 2>&1 | tail -3`
Expected: `✓ built in <time>s`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add docs/data-sources/builtin-toolsets/bitbucket.md docs/data-sources/builtin-toolsets/.nav.yml
git commit -s --no-verify -m "docs(bitbucket): add user-facing docs page + nav entry"
```

---

## Acceptance Criteria Mapping

| Spec criterion | Task |
|---|---|
| Holmes can query Bitbucket Cloud | Tasks 1-8 (toolset + 10 tools) |
| Per-project scoping (workspace + optional repo allowlist) | Tasks 1 (config) + 3 (`_check_repo_in_scope`) |
| Test Connection works from UI | Task 10 (backend helper) + Task 11 (UI button) |
| API tokens never leaked | Task 10 (`_test_bitbucket_instance_connection` redacts token) |
| Path-traversal safe input validation | Task 3 (regex helpers) |
| Size caps on diffs + file contents | Task 6 (diffs) + Task 8 (files) |
| Matches existing patterns | Mirrors `pagerduty` / `dbdash` toolset shape |
| Docs + nav | Task 12 |
| Tests | 50 unit + 3 integration = 53 new tests |

## Deployment (post-merge)

After this plan ships, to deploy to dev + prod:

1. Build + push Docker image to both ECRs with the Okta build-args (same commands used for Jenkins/PagerDuty ship).
2. **No tofu apply required** — no infra/helm changes in this plan. The new toolset is purely in the application image and registered at runtime.
3. `kubectl rollout restart deployment/holmes-holmes -n holmesgpt` in both clusters.
4. Create a Bitbucket API token for an ops bot account, store in Secrets Manager under `holmesgpt-<env>/bitbucket-<workspace>`.
5. In the Instances UI, create one or more per-project Bitbucket instances tagged to their projects.
6. Smoke-test in chat: `"List the last 3 pull requests in the <repo> repo."`
