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
        description=(
            "Atlassian API token (Bearer auth, recommended) OR Bitbucket App Password "
            "(Basic auth — set `email` to enable)."
        ),
    )
    email: Optional[str] = Field(
        default=None,
        title="Atlassian Email",
        description=(
            "Atlassian account email. When set, `api_token` is used as a Bitbucket "
            "App Password via Basic auth. Omit for Atlassian API tokens (Bearer auth)."
        ),
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

    _REPO_SLUG_RE: ToolsClassVar[re.Pattern] = re.compile(r"\A[a-z0-9._-]+\Z")
    _REF_RE: ToolsClassVar[re.Pattern] = re.compile(r"\A[A-Za-z0-9._/-]{1,255}\Z")

    def __init__(self, name: str = "bitbucket"):
        super().__init__(
            name=name,
            description="Read-only access to Bitbucket Cloud: repositories, pull requests, commits, and file contents.",
            docs_url="https://developer.atlassian.com/cloud/bitbucket/rest/",
            icon_url="https://cdn.simpleicons.org/bitbucket/0052CC",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListBitbucketRepositories(toolset=self),
                GetBitbucketRepository(toolset=self),
                ListBitbucketPullRequests(toolset=self),
                GetBitbucketPullRequest(toolset=self),
                ListBitbucketPullRequestComments(toolset=self),
                GetBitbucketPullRequestDiff(toolset=self),
                GetBitbucketCommitDiff(toolset=self),
                ListBitbucketCommits(toolset=self),
                GetBitbucketCommit(toolset=self),
                GetBitbucketFileContents(toolset=self),
            ],
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
        if self.bb_config.email:
            # Basic auth for Bitbucket App Passwords.
            import base64

            creds = base64.b64encode(
                f"{self.bb_config.email}:{self.bb_config.api_token}".encode()
            ).decode()
            return {
                "Authorization": f"Basic {creds}",
                "Accept": "application/json",
            }
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
        if not slug or ".." in slug:
            return False
        return bool(cls._REPO_SLUG_RE.match(slug))

    @classmethod
    def _validate_ref(cls, ref: str) -> bool:
        if not ref or ".." in ref:
            return False
        return bool(cls._REF_RE.match(ref))


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
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._err(
                    params,
                    f"Repository {self.toolset.bb_config.workspace}/{repo_slug} not found",
                )
            return self._err(params, str(e))
        except Exception as e:
            logging.exception("Failed to get Bitbucket repository")
            return self._err(params, str(e))


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
        state = params.get("state") or "OPEN"
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
