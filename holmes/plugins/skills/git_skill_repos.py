"""Git-synced skill repositories.

Holmes can keep one or more git repositories of skills checked out locally and
re-pull them periodically, so pushed skill changes reach a running agent without
a pod restart. Each repo is published to skill loaders through an atomic symlink
flip (a detached worktree per commit, `current` pointing at the active one), so a
catalog scan never sees a half-updated tree: `scan_skill_directory` resolves the
symlink once at scan start and walks a stable checkout.

Configured via `skill_repos` in the Holmes config, or the SKILL_REPOS env var
holding the same list as JSON (how the Helm chart passes it). Credentials are
never written to disk: the token is read from the env var named by `token_env`
and injected into the fetch URL per git invocation only.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import jwt
import requests
from pydantic import BaseModel, PrivateAttr, model_validator

from holmes.utils.env import environ_get_safe_int

SKILL_REPOS_ENV = "SKILL_REPOS"
# Root under which every repo is checked out. Defaults under the system temp dir
# because in the Helm deployment /tmp is the writable emptyDir (the root
# filesystem is read-only), and for the CLI it is always writable.
SKILL_REPOS_DIR_ENV = "SKILL_REPOS_DIR"

GIT_TIMEOUT_SECONDS = environ_get_safe_int("SKILL_REPOS_GIT_TIMEOUT_SECONDS", "120")

# The symlink each repo publishes its active checkout through.
CURRENT_LINK_NAME = "current"


def default_repos_root() -> Path:
    configured = os.environ.get(SKILL_REPOS_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "holmes-skill-repos"


class GitSkillRepo(BaseModel):
    """One git repository to sync skills from.

    `url` must not embed credentials -- it is shown in the UI and in logs.
    Authentication, one of:

    * `token_env` -- the NAME of an env var holding a static token, plus
      `username` ("oauth2" fits GitHub PATs; Bitbucket repository tokens use
      "x-token-auth").
    * GitHub App -- `github_app_id`, `github_app_installation_id`, and
      `github_app_private_key_env` (env var holding the App's PEM private key),
      all three together. A short-lived installation token is minted per sync
      and cached until near expiry, so re-pulls keep working without a static
      credential.
    """

    url: str
    # Directory name for the local checkout; also the label prefix key. Derived
    # from the URL's last path segment when omitted.
    name: Optional[str] = None
    # None -> the remote's default branch (fetches HEAD).
    branch: Optional[str] = None
    # Subdirectory inside the repo where skills live; repo root when omitted.
    sub_path: Optional[str] = None
    token_env: Optional[str] = None
    username: str = "oauth2"

    github_app_id: Optional[str] = None
    github_app_installation_id: Optional[str] = None
    github_app_private_key_env: Optional[str] = None
    # Override for GitHub Enterprise Server (e.g. https://ghe.example.com/api/v3).
    github_api_url: str = "https://api.github.com"

    # Cached installation token; minting costs two API calls, so keep it for
    # the hour GitHub grants rather than re-minting every 5-minute sync.
    _installation_token: Optional[str] = PrivateAttr(None)
    _installation_token_expiry: float = PrivateAttr(0.0)

    @model_validator(mode="after")
    def _normalize(self) -> "GitSkillRepo":
        url = self.url.strip()
        if "://" not in url:
            url = f"https://{url}"
        split = urlsplit(url)
        if split.username or split.password:
            raise ValueError(
                "skill repo url must not embed credentials; "
                "use token_env to name an environment variable instead"
            )
        self.url = url
        if not self.name:
            last_segment = split.path.rstrip("/").rsplit("/", 1)[-1]
            self.name = last_segment.removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.name) or self.name in (".", ".."):
            raise ValueError(
                f"skill repo name {self.name!r} must be a plain directory name "
                "(letters, digits, '.', '_', '-')"
            )
        if self.sub_path:
            sub = self.sub_path.strip("/")
            if ".." in Path(sub).parts:
                raise ValueError(f"skill repo sub_path {self.sub_path!r} must not contain '..'")
            self.sub_path = sub

        app_fields = (
            self.github_app_id,
            self.github_app_installation_id,
            self.github_app_private_key_env,
        )
        if any(app_fields) and not all(app_fields):
            raise ValueError(
                f"skill repo {self.name}: GitHub App auth needs github_app_id, "
                "github_app_installation_id and github_app_private_key_env together"
            )
        if any(app_fields) and self.token_env:
            raise ValueError(
                f"skill repo {self.name}: set either token_env or the github_app_* "
                "fields, not both"
            )
        return self

    def authenticated_url(self) -> str:
        """The fetch URL, with credentials injected from the environment.

        Raises when a configured credential cannot be produced (token_env names
        an unset variable, or the GitHub App mint fails): fetching a private
        repo anonymously would fail with a less actionable error, and for a
        public repo the fix is to drop the auth fields.
        """
        if self.github_app_id:
            return self._with_credentials(
                "x-access-token", self._github_app_installation_token()
            )
        if not self.token_env:
            return self.url
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"skill repo {self.name}: token_env '{self.token_env}' is not set"
            )
        return self._with_credentials(self.username, token)

    def _with_credentials(self, username: str, token: str) -> str:
        split = urlsplit(self.url)
        netloc = f"{quote(username, safe='')}:{quote(token, safe='')}@{split.netloc}"
        return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))

    def _github_app_installation_token(self) -> str:
        """A GitHub App installation token, minted on demand and cached.

        GitHub Apps have no static credential: the App's private key signs a
        short JWT, which is exchanged for an installation token valid for one
        hour. The token is cached until 5 minutes before expiry, so the
        periodic re-pull re-mints roughly once an hour.
        """
        if self._installation_token and time.time() < self._installation_token_expiry:
            return self._installation_token

        private_key = os.environ.get(self.github_app_private_key_env)  # type: ignore[arg-type]
        if not private_key:
            raise RuntimeError(
                f"skill repo {self.name}: github_app_private_key_env "
                f"'{self.github_app_private_key_env}' is not set"
            )

        now = int(time.time())
        # iat backdated 60s for clock drift; GitHub caps exp at now+10min.
        app_jwt = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.github_app_id},
            private_key,
            algorithm="RS256",
        )
        response = requests.post(
            f"{self.github_api_url.rstrip('/')}/app/installations/"
            f"{self.github_app_installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        if response.status_code != 201:
            raise RuntimeError(
                f"skill repo {self.name}: failed to mint a GitHub App installation "
                f"token (HTTP {response.status_code}): {response.text[:500]}"
            )
        token = response.json().get("token")
        if not token:
            raise RuntimeError(
                f"skill repo {self.name}: GitHub App token response carried no token"
            )
        self._installation_token = token
        # The response's expires_at is ~1h out; a fixed 55-minute cache stays
        # safely inside it without parsing GitHub's timestamp format.
        self._installation_token_expiry = time.time() + 55 * 60
        return token


def parse_skill_repos_env() -> List[GitSkillRepo]:
    """Parse the SKILL_REPOS env var (a JSON list of repo objects)."""
    raw = os.environ.get(SKILL_REPOS_ENV)
    if not raw or not raw.strip():
        return []
    try:
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise ValueError("expected a JSON list")
        return [GitSkillRepo(**entry) for entry in entries]
    except Exception:
        logging.exception(f"Failed to parse ${SKILL_REPOS_ENV}; ignoring it")
        return []


class GitSkillRepoManager:
    """Keeps the configured skill repos checked out and up to date.

    Layout per repo under `root_dir`:

        <name>/git/            bare repository (objects only, no credentials)
        <name>/worktrees/<sha> detached worktree per fetched commit
        <name>/current         symlink to the active worktree, flipped atomically

    `skill_paths` returns the `current` (plus sub_path) paths -- stable strings
    that keep pointing at the newest checkout across syncs, so they can be
    handed to config/toolsets once. Sync failures keep the previous checkout
    serving and are recorded in `last_errors`.
    """

    def __init__(
        self,
        repos: List[GitSkillRepo],
        root_dir: Optional[Path] = None,
        min_sync_interval_seconds: float = 0.0,
    ):
        # Names key the checkout directories, so two repos sharing one would
        # silently fight over a single clone and only the last one's skills
        # would ever be served. Refuse loudly instead.
        names = [repo.name for repo in repos]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(
                f"skill repos with duplicate names {sorted(duplicates)}; "
                "set a distinct 'name' on each repo"
            )
        self.repos = repos
        self.root_dir = Path(root_dir) if root_dir else default_repos_root()
        # Rate limit that sync() applies to itself, so callers on faster
        # cadences (the server refresh loop under MCP backoff) cannot multiply
        # network git fetches. The first sync always runs.
        self.min_sync_interval_seconds = min_sync_interval_seconds
        self._lock = threading.Lock()
        self._synced_once = False
        self._last_sync = 0.0
        # repo.name -> error string from the last sync attempt (absent when ok)
        self.last_errors: dict[str, str] = {}

    # ── public API ──────────────────────────────────────────────────────────

    def skill_paths(self) -> List[str]:
        """Paths to hand to the skill loaders. Syncs on first use.

        A repo that has never synced successfully is still listed: its missing
        `current` path makes the loaders report an unreadable source, which is
        exactly what keeps the HolmesCustomSkills mirror from pruning that
        repo's rows while it is broken (see FilesystemSkills.sources_ok).
        Skipping it here would make a temporarily-broken repo look like a
        deliberately-emptied one.
        """
        self._ensure_synced()
        paths = []
        for repo in self.repos:
            path = self._repo_dir(repo) / CURRENT_LINK_NAME
            if repo.sub_path:
                path = path / repo.sub_path
            paths.append(str(path))
        return paths

    def sync(self) -> None:
        """Fetch every repo and flip its `current` symlink if it moved.

        A no-op within min_sync_interval_seconds of the previous sync.
        """
        with self._lock:
            if (
                self._synced_once
                and time.time() - self._last_sync < self.min_sync_interval_seconds
            ):
                return
            self._sync_all_locked()

    def _ensure_synced(self) -> None:
        # Double-checked so concurrent cold callers do one sync, not one each.
        if self._synced_once:
            return
        with self._lock:
            if not self._synced_once:
                self._sync_all_locked()

    def _sync_all_locked(self) -> None:
        for repo in self.repos:
            try:
                self._sync_repo(repo)
                self.last_errors.pop(repo.name, None)  # type: ignore[arg-type]
            except Exception as e:
                logging.error(f"Failed to sync skill repo '{repo.name}': {e}")
                self.last_errors[repo.name] = str(e)  # type: ignore[index]
        self._synced_once = True
        self._last_sync = time.time()

    def repo_for_path(self, path: Optional[str]) -> Optional[GitSkillRepo]:
        """The repo a loaded skill's source_path belongs to, if any.

        source_path is fully resolved by the scanner, so compare against the
        resolved repo directory (root_dir itself may sit behind a symlink).
        """
        if not path:
            return None
        candidate = Path(path)
        for repo in self.repos:
            if candidate.is_relative_to(self._repo_dir(repo).resolve()):
                return repo
        return None

    # ── sync mechanics ──────────────────────────────────────────────────────

    def _repo_dir(self, repo: GitSkillRepo) -> Path:
        return self.root_dir / str(repo.name)

    def _sync_repo(self, repo: GitSkillRepo) -> None:
        repo_dir = self._repo_dir(repo)
        git_dir = repo_dir / "git"
        worktrees_dir = repo_dir / "worktrees"
        current_link = repo_dir / CURRENT_LINK_NAME

        worktrees_dir.mkdir(parents=True, exist_ok=True)
        if not (git_dir / "HEAD").exists():
            self._run_git(["git", "init", "--bare", "--quiet", str(git_dir)])

        # Prune BEFORE fetching, not right after the flip: a scan that resolved
        # `current` just before a flip is still walking the old worktree, so the
        # superseded checkout must survive until the next sync (a full refresh
        # interval), by which time any in-flight scan has long finished.
        self._prune_worktrees(git_dir, worktrees_dir, current_link)

        ref = repo.branch or "HEAD"
        # Fetch by URL instead of a configured remote so credentials are never
        # written into .git/config.
        self._run_git(
            [
                "git",
                "--git-dir",
                str(git_dir),
                "fetch",
                "--depth",
                "1",
                "--quiet",
                repo.authenticated_url(),
                ref,
            ]
        )
        sha = self._run_git(
            ["git", "--git-dir", str(git_dir), "rev-parse", "FETCH_HEAD"]
        ).strip()

        if self._current_sha(current_link) == sha:
            return

        worktree = worktrees_dir / sha
        if not worktree.exists():
            self._run_git(
                [
                    "git",
                    "--git-dir",
                    str(git_dir),
                    "worktree",
                    "add",
                    "--detach",
                    "--force",
                    "--quiet",
                    str(worktree),
                    sha,
                ]
            )

        # Atomic flip: build the new symlink beside the old one, then rename over
        # it, so readers only ever see the old checkout or the new one.
        tmp_link = repo_dir / f".{CURRENT_LINK_NAME}.tmp"
        if tmp_link.is_symlink() or tmp_link.exists():
            tmp_link.unlink()
        os.symlink(worktree, tmp_link)
        os.replace(tmp_link, current_link)
        logging.info(f"Skill repo '{repo.name}' updated to {sha[:12]} ({repo.url})")

    @staticmethod
    def _current_sha(current_link: Path) -> Optional[str]:
        if not current_link.is_symlink():
            return None
        return Path(os.readlink(current_link)).name

    def _prune_worktrees(
        self, git_dir: Path, worktrees_dir: Path, current_link: Path
    ) -> None:
        """Remove checkouts of superseded commits, sparing the active one."""
        keep_sha = self._current_sha(current_link)
        removed = False
        for entry in worktrees_dir.iterdir():
            if entry.name == keep_sha or not entry.is_dir():
                continue
            try:
                shutil.rmtree(entry)
                removed = True
            except OSError as e:
                logging.warning(f"Failed to remove old skill repo worktree {entry}: {e}")
        if not removed:
            return
        try:
            self._run_git(["git", "--git-dir", str(git_dir), "worktree", "prune"])
        except Exception as e:
            logging.warning(f"git worktree prune failed for {git_dir}: {e}")

    @staticmethod
    def _run_git(cmd: List[str]) -> str:
        env = {
            **os.environ,
            # Never hang on a credential prompt inside a server.
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired:
            # Never re-raise TimeoutExpired: its message embeds the full command
            # line, credential-bearing fetch URL included.
            raise RuntimeError(
                f"{' '.join(cmd[:3])}... timed out after {GIT_TIMEOUT_SECONDS}s"
            ) from None
        if result.returncode != 0:
            # stderr may echo the URL of a failed fetch; strip any userinfo so a
            # token never reaches the logs.
            stderr = re.sub(r"://[^/@\s]+@", "://", result.stderr.strip())
            raise RuntimeError(
                f"{' '.join(cmd[:3])}... failed (exit {result.returncode}): {stderr}"
            )
        return result.stdout
