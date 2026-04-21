"""
Git provider abstraction for GitHub and GitLab.

Handles authenticated cloning and pull request creation.
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class GitProvider(ABC):
    """Abstract base for git hosting providers."""

    def __init__(self, token: str):
        self.token = token

    @abstractmethod
    def get_authenticated_clone_url(self, repo_url: str) -> str:
        """Return a clone URL with embedded authentication."""

    @abstractmethod
    def create_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> str:
        """Create a pull request and return its URL."""


class GitHubProvider(GitProvider):
    """GitHub implementation using the REST API."""

    API_BASE = "https://api.github.com"

    def get_authenticated_clone_url(self, repo_url: str) -> str:
        return repo_url.replace(
            "https://github.com",
            f"https://x-access-token:{self.token}@github.com",
        )

    def _parse_owner_repo(self, repo_url: str) -> tuple[str, str]:
        """Extract owner and repo name from a GitHub URL."""
        match = re.match(
            r"https?://github\.com/([^/]+)/([^/.]+?)(?:\.git)?/?$", repo_url
        )
        if not match:
            raise ValueError(f"Cannot parse GitHub repository URL: {repo_url}")
        return match.group(1), match.group(2)

    def create_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> str:
        owner, repo = self._parse_owner_repo(repo_url)
        url = f"{self.API_BASE}/repos/{owner}/{repo}/pulls"

        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
                "draft": draft,
            },
            timeout=30,
        )

        if response.status_code == 201:
            pr_data = response.json()
            pr_url = pr_data["html_url"]
            logger.info(f"Created GitHub PR: {pr_url}")
            return pr_url

        raise RuntimeError(
            f"Failed to create GitHub PR: {response.status_code} {response.text}"
        )


class GitLabProvider(GitProvider):
    """GitLab implementation using the REST API."""

    def __init__(self, token: str, api_base: Optional[str] = None):
        super().__init__(token)
        self.api_base = (api_base or "https://gitlab.com").rstrip("/")

    def get_authenticated_clone_url(self, repo_url: str) -> str:
        # Extract the host from the repo URL to handle self-hosted GitLab
        match = re.match(r"(https?://[^/]+)", repo_url)
        if not match:
            raise ValueError(f"Cannot parse GitLab repository URL: {repo_url}")
        host = match.group(1)
        return repo_url.replace(
            host, f"{host.split('://')[0]}://oauth2:{self.token}@{host.split('://')[1]}"
        )

    def _parse_project_path(self, repo_url: str) -> str:
        """Extract the project path from a GitLab URL and URL-encode it."""
        match = re.match(r"https?://[^/]+/(.+?)(?:\.git)?/?$", repo_url)
        if not match:
            raise ValueError(f"Cannot parse GitLab repository URL: {repo_url}")
        return requests.utils.quote(match.group(1), safe="")

    def _get_api_base(self, repo_url: str) -> str:
        """Extract the API base URL from the repo URL."""
        match = re.match(r"(https?://[^/]+)", repo_url)
        if not match:
            raise ValueError(f"Cannot parse GitLab repository URL: {repo_url}")
        return f"{match.group(1)}/api/v4"

    def create_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> str:
        project_path = self._parse_project_path(repo_url)
        api_base = self._get_api_base(repo_url)
        url = f"{api_base}/projects/{project_path}/merge_requests"

        # GitLab uses "description" instead of "body"
        payload: dict = {
            "source_branch": head_branch,
            "target_branch": base_branch,
            "title": ("Draft: " + title) if draft else title,
            "description": body,
        }

        response = requests.post(
            url,
            headers={
                "PRIVATE-TOKEN": self.token,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        if response.status_code == 201:
            mr_data = response.json()
            mr_url = mr_data["web_url"]
            logger.info(f"Created GitLab MR: {mr_url}")
            return mr_url

        raise RuntimeError(
            f"Failed to create GitLab MR: {response.status_code} {response.text}"
        )


def create_git_provider(
    provider_type: str,
    token: str,
    gitlab_api_base: Optional[str] = None,
) -> GitProvider:
    """Factory for git providers."""
    if provider_type == "github":
        return GitHubProvider(token=token)
    elif provider_type == "gitlab":
        return GitLabProvider(token=token, api_base=gitlab_api_base)
    else:
        raise ValueError(
            f"Unsupported git provider: '{provider_type}'. Use 'github' or 'gitlab'."
        )
