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
            return True, ""  # _health_check added in Task 2.
        except Exception as e:
            return False, f"Failed to configure Bitbucket toolset: {e}"
