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
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, model_validator

SKILL_REPOS_ENV = "SKILL_REPOS"
# Root under which every repo is checked out. Defaults under the system temp dir
# because in the Helm deployment /tmp is the writable emptyDir (the root
# filesystem is read-only), and for the CLI it is always writable.
SKILL_REPOS_DIR_ENV = "SKILL_REPOS_DIR"

GIT_TIMEOUT_SECONDS = int(os.environ.get("SKILL_REPOS_GIT_TIMEOUT_SECONDS", "120"))

# The symlink each repo publishes its active checkout through.
CURRENT_LINK_NAME = "current"


def default_repos_root() -> Path:
    configured = os.environ.get(SKILL_REPOS_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "holmes-skill-repos"


class GitSkillRepo(BaseModel):
    """One git repository to sync skills from.

    `url` must not embed credentials -- it is shown in the UI and in logs. Use
    `token_env` (the NAME of an env var holding the token) plus `username`
    ("oauth2" fits GitHub PATs; Bitbucket repository tokens use "x-token-auth").
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
            self.name = re.sub(r"\.git$", "", last_segment)
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
        return self

    def authenticated_url(self) -> str:
        """The fetch URL, with credentials injected from the environment.

        Raises when token_env names a variable that is not set: fetching a
        private repo anonymously would fail with a less actionable error, and
        for a public repo the fix is to drop token_env.
        """
        if not self.token_env:
            return self.url
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"skill repo {self.name}: token_env '{self.token_env}' is not set"
            )
        split = urlsplit(self.url)
        netloc = f"{quote(self.username, safe='')}:{quote(token, safe='')}@{split.netloc}"
        return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))


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

    def __init__(self, repos: List[GitSkillRepo], root_dir: Optional[Path] = None):
        self.repos = repos
        self.root_dir = Path(root_dir) if root_dir else default_repos_root()
        self._lock = threading.Lock()
        self._synced_once = False
        # repo.name -> error string from the last sync attempt (absent when ok)
        self.last_errors: dict[str, str] = {}

    # ── public API ──────────────────────────────────────────────────────────

    def skill_paths(self) -> List[str]:
        """Paths to hand to the skill loaders. Syncs on first use."""
        self.ensure_synced()
        paths = []
        for repo in self.repos:
            path = self._repo_dir(repo) / CURRENT_LINK_NAME
            if repo.sub_path:
                path = path / repo.sub_path
            paths.append(str(path))
        return paths

    def ensure_synced(self) -> None:
        if self._synced_once:
            return
        self.sync()

    def sync(self) -> None:
        """Fetch every repo and flip its `current` symlink if it moved."""
        with self._lock:
            for repo in self.repos:
                try:
                    self._sync_repo(repo)
                    self.last_errors.pop(repo.name, None)  # type: ignore[arg-type]
                except Exception as e:
                    logging.error(f"Failed to sync skill repo '{repo.name}': {e}")
                    self.last_errors[repo.name] = str(e)  # type: ignore[index]
            self._synced_once = True

    def repo_for_path(self, path: Optional[str]) -> Optional[GitSkillRepo]:
        """The repo a loaded skill's source_path belongs to, if any.

        source_path is fully resolved by the scanner, so compare against the
        resolved repo directory.
        """
        if not path:
            return None
        for repo in self.repos:
            repo_dir = self._repo_dir(repo)
            try:
                resolved_repo_dir = repo_dir.resolve()
            except OSError:
                continue
            candidate = Path(path)
            if candidate.is_relative_to(repo_dir) or candidate.is_relative_to(
                resolved_repo_dir
            ):
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

        self._prune_worktrees(git_dir, worktrees_dir, keep_sha=sha)

    @staticmethod
    def _current_sha(current_link: Path) -> Optional[str]:
        if not current_link.is_symlink():
            return None
        return Path(os.readlink(current_link)).name

    def _prune_worktrees(self, git_dir: Path, worktrees_dir: Path, keep_sha: str) -> None:
        """Remove checkouts of superseded commits (a request mid-scan is on a
        resolved path, so the small race window only affects a scan that started
        before the flip; retirees are only removed after the flip)."""
        for entry in worktrees_dir.iterdir():
            if entry.name == keep_sha or not entry.is_dir():
                continue
            try:
                shutil.rmtree(entry)
            except OSError as e:
                logging.warning(f"Failed to remove old skill repo worktree {entry}: {e}")
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
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
        )
        if result.returncode != 0:
            # stderr may echo the URL of a failed fetch; strip any userinfo so a
            # token never reaches the logs.
            stderr = re.sub(r"://[^/@\s]+@", "://", result.stderr.strip())
            raise RuntimeError(
                f"{' '.join(cmd[:3])}... failed (exit {result.returncode}): {stderr}"
            )
        return result.stdout
