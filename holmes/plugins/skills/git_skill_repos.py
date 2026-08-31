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

import base64
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
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import jwt
import requests
from pydantic import BaseModel, PrivateAttr, ValidationError, model_validator

from holmes.utils.env import environ_get_safe_int

SKILL_REPOS_ENV = "SKILL_REPOS"
# Root under which every repo is checked out. Defaults under the system temp dir
# because in the Helm deployment /tmp is the writable emptyDir (the root
# filesystem is read-only), and for the CLI it is always writable.
SKILL_REPOS_DIR_ENV = "SKILL_REPOS_DIR"

# 60s, not 120: this timeout also bounds the CLI's inline sync, which sits in
# front of every `holmes ask`, and the first server-side sync that a cold
# request may wait on (see _ensure_synced).
GIT_TIMEOUT_SECONDS = environ_get_safe_int("SKILL_REPOS_GIT_TIMEOUT_SECONDS", "60")

# How long a request-path caller waits for the very first sync before giving up
# and serving without the repo's skills. Bounded so a slow or unreachable remote
# delays a request by seconds, not by a full git timeout per repo.
FIRST_SYNC_WAIT_SECONDS = environ_get_safe_int(
    "SKILL_REPOS_FIRST_SYNC_WAIT_SECONDS", "5"
)

# The symlink each repo publishes its active checkout through.
CURRENT_LINK_NAME = "current"

# Schemes a skill repo url may use. Anything else is a typo or a paste of an
# unsupported form (ssh://, git@host:path) -- rejected with a message that says
# so, rather than handed to git to fail obscurely. Note `file://` is here for
# tests and local checkouts; `http://` only survives validation for repos with
# no credentials attached (see _normalize).
ALLOWED_URL_SCHEMES = ("https", "http", "file")


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
    # from the URL's last path segment when omitted -- so it is always a real
    # name after validation, never None, and callers can index dicts with it.
    name: str = ""
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
        # `git@host:org/repo.git` has no scheme but is not a bare host either;
        # prefixing https:// turns it into a credential-bearing url and the
        # error below would tell the user to use token_env, which is not the
        # problem. Name the real one.
        if "://" not in url and re.match(r"^[^/]+@[^/]+:", url):
            raise ValueError(
                f"skill repo url {url!r} looks like an SSH url. SSH is not "
                "supported (Holmes holds no ssh key); use the https:// url with "
                "token_env or the github_app_* fields instead"
            )
        if "://" not in url:
            url = f"https://{url}"
        split = urlsplit(url)
        if split.scheme == "ssh":
            raise ValueError(
                f"skill repo url {url!r} uses ssh://, which is not supported "
                "(Holmes holds no ssh key); use the https:// url with token_env "
                "or the github_app_* fields instead"
            )
        if split.scheme not in ALLOWED_URL_SCHEMES:
            raise ValueError(
                f"skill repo url scheme {split.scheme!r} is not supported; "
                f"use one of {', '.join(ALLOWED_URL_SCHEMES)}"
            )
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
                raise ValueError(
                    f"skill repo sub_path {self.sub_path!r} must not contain '..'"
                )
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
        # A credential must never cross the network in cleartext: authenticated
        # fetches embed the token in the URL, and the App JWT rides an HTTP
        # header to github_api_url.
        if (self.token_env or any(app_fields)) and split.scheme != "https":
            raise ValueError(
                f"skill repo {self.name}: authenticated repos must use an https:// "
                f"url, got {split.scheme}://"
            )
        if any(app_fields) and urlsplit(self.github_api_url).scheme != "https":
            raise ValueError(
                f"skill repo {self.name}: github_api_url must use https://, "
                f"got {self.github_api_url!r}"
            )
        return self

    def credential_env(self) -> Dict[str, str]:
        """Env vars carrying the credential for one git invocation.

        The credential rides an `http.<url>.extraHeader` config passed through
        GIT_CONFIG_* env vars rather than being injected into the fetch URL as
        userinfo. Two reasons:

        * argv is world-readable. `/proc/<pid>/cmdline` is mode 444 and `ps`
          shows it, so a token in the fetch URL is visible to any process in
          the pod (a debug container, an exec-auditing agent). `environ` is
          mode 400 -- owner only -- and is not shown by `ps`.
        * The config key is scoped to this repo's URL, so git will not attach
          the header if the remote redirects us to another host.

        Returns {} for a repo with no auth configured. Raises when a configured
        credential cannot be produced (token_env names an unset variable, or the
        GitHub App mint fails): fetching a private repo anonymously would fail
        with a less actionable error, and for a public repo the fix is to drop
        the auth fields.
        """
        if self.github_app_id:
            return self._header_env(
                "x-access-token", self._github_app_installation_token()
            )
        if not self.token_env:
            return {}
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"skill repo {self.name}: token_env '{self.token_env}' is not set"
            )
        return self._header_env(self.username, token)

    def _header_env(self, username: str, token: str) -> Dict[str, str]:
        basic = base64.b64encode(f"{username}:{token}".encode()).decode()
        # APPEND to any GIT_CONFIG_* already in the environment rather than
        # starting at index 0. The mechanism is a shared numbered list, so
        # writing KEY_0/COUNT=1 would overwrite the first existing entry and
        # drop the rest -- and this environment is a real one to inherit from:
        # a corporate proxy, a CA path, or url.insteadOf rewrites are commonly
        # injected this way, and silently losing them breaks the fetch in a way
        # that looks like a network fault.
        index = self._existing_git_config_count()
        return {
            "GIT_CONFIG_COUNT": str(index + 1),
            # URL-scoped so the header is not resent on a cross-host redirect.
            f"GIT_CONFIG_KEY_{index}": f"http.{self.url}.extraHeader",
            f"GIT_CONFIG_VALUE_{index}": f"Authorization: Basic {basic}",
        }

    @staticmethod
    def _existing_git_config_count() -> int:
        """How many GIT_CONFIG_* entries the ambient environment already holds.

        Garbage (or an absent var) means "none": git itself rejects a
        non-numeric GIT_CONFIG_COUNT, so treating it as 0 replaces one broken
        config with a working one rather than inventing a worse failure.
        """
        try:
            return max(int(os.environ.get("GIT_CONFIG_COUNT", "0")), 0)
        except ValueError:
            logging.warning(
                "Ignoring non-numeric GIT_CONFIG_COUNT while building git credentials"
            )
            return 0

    def _github_app_installation_token(self) -> str:
        """A GitHub App installation token, minted on demand and cached.

        GitHub Apps have no static credential: the App's private key signs a
        short JWT, which is exchanged for an installation token valid for one
        hour. The token is cached until 5 minutes before expiry, so the
        periodic re-pull re-mints roughly once an hour.
        """
        if self._installation_token and time.time() < self._installation_token_expiry:
            return self._installation_token

        private_key = os.environ.get(self.github_app_private_key_env or "")
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
    except ValidationError as e:
        # NOT logging.exception: pydantic renders the rejected input into the
        # error string, and one of the things validation rejects is a url with
        # credentials embedded in it -- so the generic path would log the very
        # token the check exists to refuse. errors(include_input=False) keeps
        # the actionable part (which field, which rule) without the value.
        logging.error(
            f"Failed to parse ${SKILL_REPOS_ENV}; ignoring it. "
            f"Validation errors: {e.errors(include_input=False, include_url=False)}"
        )
        return []
    except Exception as e:
        # json.JSONDecodeError reports a position, not the payload, so the
        # message is safe to log; the traceback is not needed either way.
        logging.error(f"Failed to parse ${SKILL_REPOS_ENV}; ignoring it: {e}")
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

        A repo is listed once it has ever published a checkout -- i.e. its
        `current` symlink exists -- and is omitted until then.

        The distinction matters because an unreadable path makes the loaders
        report an unreadable source, which stops the HolmesCustomSkills mirror
        from pruning ANY row for the whole cluster (see
        FilesystemSkills.sources_ok). Both halves are deliberate:

        * A repo that HAS a checkout stays listed even when this sync failed.
          Its `current` still points at the last good worktree, so its skills
          keep loading; and if the checkout itself has gone missing (a wiped
          /tmp, a dangling symlink), the path is unreadable and pruning is
          correctly held off rather than wiping rows for skills that still
          exist upstream.
        * A repo that has NEVER published a checkout is omitted. It has
          contributed no rows, so omitting it cannot prune anything of its own
          -- while listing it would veto pruning cluster-wide for as long as it
          stays broken. A permanently broken repo (typo'd sub_path, wrong
          branch, revoked token) would otherwise freeze the mirror forever,
          silently, for every other skill source too.
        """
        self._ensure_synced()
        paths = []
        for repo in self.repos:
            current = self._repo_dir(repo) / CURRENT_LINK_NAME
            # Silent: this runs on every request and several times per toolset
            # build, so a log line here becomes many per second. _sync_all_locked
            # reports the omission once per sync cycle instead.
            if not current.is_symlink():
                continue
            path = current / repo.sub_path if repo.sub_path else current
            paths.append(str(path))
        return paths

    def unsynced_repos(self) -> Dict[str, str]:
        """repo name -> reason, for every configured repo with no checkout.

        These contribute no skills, so callers that report status (the mirror
        sync, the admin API) can surface them instead of leaving the failure in
        the logs only.
        """
        broken = {}
        for repo in self.repos:
            if (self._repo_dir(repo) / CURRENT_LINK_NAME).is_symlink():
                continue
            broken[repo.name] = self.last_errors.get(
                repo.name, "first sync has not completed"
            )
        return broken

    def display_path(self, source_path: Optional[str]) -> Optional[str]:
        """`source_path` with the worktree segment folded back to `current`.

        A scan resolves the `current` symlink, so every loaded skill's path
        carries the commit sha of the checkout it came from -- it changes on
        every push and means nothing to a user reading it in the UI. Map it
        back onto the stable published path.
        """
        if not source_path:
            return source_path
        candidate = Path(source_path)
        for repo in self.repos:
            worktrees = (self._repo_dir(repo) / "worktrees").resolve()
            if not candidate.is_relative_to(worktrees):
                continue
            # <worktrees>/<sha>/rest... -> <repo dir>/current/rest...
            rest = candidate.relative_to(worktrees).parts[1:]
            return str(self._repo_dir(repo).joinpath(CURRENT_LINK_NAME, *rest))
        return source_path

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
        # Bounded wait, not `with self._lock`: on the server the startup warmup
        # thread holds this lock for the length of the first clone, and a chat
        # request arriving meanwhile used to block on it for up to a git timeout
        # per repo. Waiting briefly still lets a fast clone finish before the
        # first request is answered; past that the request is served without the
        # repo's skills (skill_paths omits repos with no checkout) and the next
        # request picks them up.
        if not self._lock.acquire(timeout=FIRST_SYNC_WAIT_SECONDS):
            logging.warning(
                "Skill repo sync still running after "
                f"{FIRST_SYNC_WAIT_SECONDS}s; continuing without git-synced skills"
            )
            return
        try:
            if not self._synced_once:
                self._sync_all_locked()
        finally:
            self._lock.release()

    def _sync_all_locked(self) -> None:
        for repo in self.repos:
            try:
                self._sync_repo(repo)
                self.last_errors.pop(repo.name, None)
            except Exception as e:
                logging.error(f"Failed to sync skill repo '{repo.name}': {e}")
                self.last_errors[repo.name] = str(e)
        self._synced_once = True
        self._last_sync = time.time()
        # Once per cycle, not once per skill_paths() call: a repo with no
        # checkout contributes nothing, and the reason has to be visible
        # somewhere without drowning the log.
        for name, reason in self.unsynced_repos().items():
            logging.warning(
                f"Skill repo '{name}' has no checkout, so its skills are not "
                f"loaded: {reason}"
            )

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
        return self.root_dir / repo.name

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
        # written into .git/config, and pass the credential itself through env
        # (see GitSkillRepo.credential_env) so it stays out of argv.
        self._run_git(
            [
                "git",
                "--git-dir",
                str(git_dir),
                "fetch",
                "--depth",
                "1",
                "--no-tags",
                "--quiet",
                repo.url,
                ref,
            ],
            extra_env=repo.credential_env(),
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
        self._collect_garbage(git_dir)

    def _collect_garbage(self, git_dir: Path) -> None:
        """Drop objects no live checkout needs any more.

        Without this the object store only ever grows: each `fetch --depth 1`
        of a new commit adds its objects and nothing removes the previous
        commit's, so a long-running pod accumulates one checkout's worth of
        objects per push -- on the Helm deployment, inside a /tmp emptyDir with
        a sizeLimit, which the kubelet enforces by EVICTING the pod.

        Reachability comes from the worktrees, not from refs: this repo has no
        refs at all (fetch-by-url only writes FETCH_HEAD), and git counts every
        worktree's detached HEAD as a root. So this runs after the flip, never
        before -- at which point the active checkout and the one superseded
        worktree still in its grace period are both roots and both survive.

        Only on a flip, so the cost lands once per push rather than on every
        no-op sync. Best-effort: a gc failure must not fail the sync, it just
        means the store stays large for another cycle.
        """
        try:
            self._run_git(
                [
                    "git",
                    "--git-dir",
                    str(git_dir),
                    "gc",
                    "--prune=now",
                    "--quiet",
                ]
            )
        except Exception as e:
            logging.warning(f"git gc failed for {git_dir}: {e}")

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
                logging.warning(
                    f"Failed to remove old skill repo worktree {entry}: {e}"
                )
        if not removed:
            return
        try:
            self._run_git(["git", "--git-dir", str(git_dir), "worktree", "prune"])
        except Exception as e:
            logging.warning(f"git worktree prune failed for {git_dir}: {e}")

    @staticmethod
    def _run_git(cmd: List[str], extra_env: Optional[Dict[str, str]] = None) -> str:
        env = {
            **os.environ,
            # Never hang on a credential prompt inside a server.
            "GIT_TERMINAL_PROMPT": "0",
            **(extra_env or {}),
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
            # With the credential in a header there is no username/password for
            # git to retry with, so a rejected credential surfaces as git asking
            # for one. On its own that reads like "no auth configured", which
            # sends people looking in the wrong place.
            if (
                "could not read Username" in stderr
                or "terminal prompts disabled" in stderr
            ):
                stderr += (
                    " (the server refused the configured credential -- check that the"
                    " token is valid and still has read access to this repo)"
                )
            raise RuntimeError(
                f"{' '.join(cmd[:3])}... failed (exit {result.returncode}): {stderr}"
            )
        return result.stdout
