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
        except Exception as e:
            return False, f"Failed to configure Bitbucket toolset: {e}"
        return self._health_check()

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
