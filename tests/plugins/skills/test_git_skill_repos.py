import base64
import os
import shutil
from pathlib import Path

import jwt
import pytest
import responses as responses_lib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from holmes.plugins.skills.git_skill_repos import (
    CURRENT_LINK_NAME,
    SKILL_REPOS_ENV,
    GitSkillRepo,
    GitSkillRepoManager,
    parse_skill_repos_env,
)
from holmes.plugins.skills.skill_loader import load_filesystem_skills
from tests.git_skill_repo_utils import (
    commit_all as _commit_all,
    make_skill_repo as _make_skill_repo,
    write_skills as _write_skills,
)


@pytest.fixture(autouse=True)
def _hermetic_git_config(monkeypatch):
    """Clear ambient GIT_CONFIG_* so credential-env assertions are reproducible.

    Real environments (CI runners, proxied sandboxes) inject git config through
    this numbered-list mechanism, and the index our entry lands at depends on
    how many are already set. Tests that care about the appending behaviour set
    the ambient entries themselves.
    """
    for key in list(os.environ):
        if key.startswith("GIT_CONFIG"):
            monkeypatch.delenv(key, raising=False)


def _manager_for(repo: Path, tmp_path: Path, **repo_kwargs) -> GitSkillRepoManager:
    config = GitSkillRepo(url=f"file://{repo}", **repo_kwargs)
    return GitSkillRepoManager([config], root_dir=tmp_path / "checkouts")


def test_sync_clones_and_skills_load(tmp_path: Path):
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "check coredns"})
    manager = _manager_for(repo, tmp_path)

    # skill_paths syncs lazily on first use -- no explicit sync() needed.
    loaded = load_filesystem_skills(manager.skill_paths())

    names = {s.name for s in loaded.skills}
    assert "dns-debug" in names
    assert manager.last_errors == {}


def test_resync_picks_up_new_and_edited_skills(tmp_path: Path):
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "check coredns"})
    manager = _manager_for(repo, tmp_path)
    paths = manager.skill_paths()

    _write_skills(
        repo, {"dns-debug": "check coredns AND kube-proxy", "oom": "check limits"}
    )
    _commit_all(repo, "update skills")
    manager.sync()

    # The same path strings stay valid across syncs (the symlink flipped).
    loaded = load_filesystem_skills(paths)
    by_name = {s.name: s for s in loaded.skills}
    assert "oom" in by_name
    assert "kube-proxy" in by_name["dns-debug"].content


def test_resync_removes_deleted_skills_and_prunes_old_worktrees(tmp_path: Path):
    repo = _make_skill_repo(
        tmp_path / "repo", {"dns-debug": "check coredns", "oom": "check limits"}
    )
    manager = _manager_for(repo, tmp_path)
    paths = manager.skill_paths()
    assert {s.name for s in load_filesystem_skills(paths).skills} >= {
        "dns-debug",
        "oom",
    }

    shutil.rmtree(repo / "oom")
    _commit_all(repo, "drop oom skill")
    manager.sync()

    loaded = load_filesystem_skills(paths)
    assert "oom" not in {s.name for s in loaded.skills}

    # The superseded worktree survives one sync (a scan that resolved `current`
    # just before the flip may still be walking it) and is pruned on the next.
    worktrees_dir = tmp_path / "checkouts" / "repo" / "worktrees"
    assert len(list(worktrees_dir.iterdir())) == 2
    manager.sync()
    assert len(list(worktrees_dir.iterdir())) == 1


def test_sub_path_scopes_the_scan(tmp_path: Path):
    repo = _make_skill_repo(tmp_path / "repo", {"inside": "in"}, sub_path="skills")
    _write_skills(repo, {"outside": "out"})
    _commit_all(repo, "add outside skill")
    manager = _manager_for(repo, tmp_path, sub_path="skills")

    names = {s.name for s in load_filesystem_skills(manager.skill_paths()).skills}
    assert "inside" in names
    assert "outside" not in names


def test_failed_fetch_keeps_previous_checkout(tmp_path: Path):
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "check coredns"})
    manager = _manager_for(repo, tmp_path)
    paths = manager.skill_paths()
    assert manager.last_errors == {}

    # Make the remote unreachable and re-sync: the old checkout must keep serving.
    shutil.rmtree(repo)
    manager.sync()

    assert "repo" in manager.last_errors
    assert "dns-debug" in {s.name for s in load_filesystem_skills(paths).skills}


def test_missing_token_env_is_an_error_not_a_crash(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MISSING_SKILL_TOKEN", raising=False)
    # The credential check fires before any fetch, so no network is touched.
    repo = GitSkillRepo(
        url="https://github.com/acme/skills.git", token_env="MISSING_SKILL_TOKEN"
    )
    manager = GitSkillRepoManager([repo], root_dir=tmp_path / "checkouts")

    manager.sync()

    assert "MISSING_SKILL_TOKEN" in manager.last_errors["skills"]


def test_token_env_becomes_a_url_scoped_auth_header_not_a_url(monkeypatch):
    """The credential rides env-passed git config, never argv.

    A token in the fetch URL would sit in /proc/<pid>/cmdline (mode 444) and in
    `ps` output for every process in the pod; GIT_CONFIG_* lands in `environ`
    (mode 400) instead. The config key is URL-scoped so a cross-host redirect
    does not carry the header along.
    """
    monkeypatch.setenv("SKILL_TOKEN", "s3cr3t/+")
    repo = GitSkillRepo(
        url="https://github.com/acme/skills.git", token_env="SKILL_TOKEN"
    )

    env = repo.credential_env()

    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == (
        "http.https://github.com/acme/skills.git.extraHeader"
    )
    scheme, encoded = env["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: ").split()
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == "oauth2:s3cr3t/+"
    # The stored URL never carries the credential.
    assert "s3cr3t" not in repo.url


def test_public_repo_gets_no_credential_env():
    assert GitSkillRepo(url="https://github.com/acme/public.git").credential_env() == {}


def test_repo_for_path_maps_checkout_to_repo(tmp_path: Path):
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "x"})
    manager = _manager_for(repo, tmp_path)

    loaded = load_filesystem_skills(manager.skill_paths())
    skill = next(s for s in loaded.skills if s.name == "dns-debug")

    matched = manager.repo_for_path(skill.source_path)
    assert matched is not None and matched.url == f"file://{repo}"
    assert manager.repo_for_path("/somewhere/else/SKILL.md") is None


def test_url_with_embedded_credentials_is_rejected():
    with pytest.raises(ValueError):
        GitSkillRepo(url="https://oauth2:token@github.com/acme/skills.git")


def test_name_derived_from_url():
    assert GitSkillRepo(url="github.com/acme/holmes-skills.git").name == "holmes-skills"
    assert GitSkillRepo(url="https://github.com/acme/skills").name == "skills"


def test_sub_path_traversal_rejected():
    with pytest.raises(ValueError):
        GitSkillRepo(url="github.com/acme/skills.git", sub_path="../outside")


def test_parse_skill_repos_env(monkeypatch):
    monkeypatch.setenv(
        SKILL_REPOS_ENV,
        '[{"url": "github.com/acme/skills.git", "branch": "main", '
        '"sub_path": "skills", "token_env": "TOK"}]',
    )
    repos = parse_skill_repos_env()
    assert len(repos) == 1
    assert repos[0].url == "https://github.com/acme/skills.git"
    assert repos[0].branch == "main"
    assert repos[0].sub_path == "skills"
    assert repos[0].token_env == "TOK"

    monkeypatch.setenv(SKILL_REPOS_ENV, "not json")
    assert parse_skill_repos_env() == []

    monkeypatch.delenv(SKILL_REPOS_ENV)
    assert parse_skill_repos_env() == []


# ── GitHub App authentication (installation tokens minted per sync) ──


def _rsa_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _github_app_repo(**overrides) -> GitSkillRepo:
    fields = {
        "url": "https://github.com/acme/skills.git",
        "github_app_id": "12345",
        "github_app_installation_id": "67890",
        "github_app_private_key_env": "GH_APP_KEY",
        **overrides,
    }
    return GitSkillRepo(**fields)


def test_github_app_mints_installation_token_for_fetch(monkeypatch, responses):
    monkeypatch.setenv("GH_APP_KEY", _rsa_private_key_pem())
    responses.add(
        responses_lib.POST,
        "https://api.github.com/app/installations/67890/access_tokens",
        json={"token": "ghs_minted", "expires_at": "2099-01-01T00:00:00Z"},
        status=201,
    )
    repo = _github_app_repo()

    env = repo.credential_env()

    encoded = env["GIT_CONFIG_VALUE_0"].split()[-1]
    assert base64.b64decode(encoded).decode() == "x-access-token:ghs_minted"
    # The exchange was authorized with a JWT issued by the App.
    auth_header = responses.calls[0].request.headers["Authorization"]
    assert auth_header.startswith("Bearer ")
    claims = jwt.decode(
        auth_header.removeprefix("Bearer "), options={"verify_signature": False}
    )
    assert claims["iss"] == "12345"


def test_github_app_token_is_cached_across_syncs(monkeypatch, responses):
    monkeypatch.setenv("GH_APP_KEY", _rsa_private_key_pem())
    responses.add(
        responses_lib.POST,
        "https://api.github.com/app/installations/67890/access_tokens",
        json={"token": "ghs_minted"},
        status=201,
    )
    repo = _github_app_repo()

    first = repo.credential_env()
    second = repo.credential_env()

    assert first == second
    assert len(responses.calls) == 1


def test_github_app_mint_failure_is_a_sync_error_not_a_crash(
    tmp_path, monkeypatch, responses
):
    monkeypatch.setenv("GH_APP_KEY", _rsa_private_key_pem())
    responses.add(
        responses_lib.POST,
        "https://api.github.com/app/installations/67890/access_tokens",
        json={"message": "Integration not found"},
        status=404,
    )
    repo = _github_app_repo(name="app-repo")
    manager = GitSkillRepoManager([repo], root_dir=tmp_path / "checkouts")

    manager.sync()

    assert "installation" in manager.last_errors["app-repo"]


def test_github_app_fields_are_all_or_none():
    with pytest.raises(ValueError):
        GitSkillRepo(url="github.com/acme/skills.git", github_app_id="12345")


def test_github_app_and_token_env_are_mutually_exclusive():
    with pytest.raises(ValueError):
        _github_app_repo(token_env="TOK")


def test_duplicate_repo_names_are_rejected():
    repos = [
        GitSkillRepo(url="https://github.com/team-a/skills.git"),
        GitSkillRepo(url="https://github.com/team-b/skills.git"),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        GitSkillRepoManager(repos)


def test_sync_is_rate_limited_but_first_sync_always_runs(tmp_path: Path):
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "old"})
    manager = GitSkillRepoManager(
        [GitSkillRepo(url=f"file://{repo}")],
        root_dir=tmp_path / "checkouts",
        min_sync_interval_seconds=3600,
    )
    # First sync runs regardless of the interval.
    paths = manager.skill_paths()
    assert "dns-debug" in {s.name for s in load_filesystem_skills(paths).skills}

    _write_skills(repo, {"dns-debug": "new steps"})
    _commit_all(repo, "update")

    # Within the interval sync() is a no-op...
    manager.sync()
    skill = next(
        s for s in load_filesystem_skills(paths).skills if s.name == "dns-debug"
    )
    assert "new steps" not in skill.content

    # ...and once it elapses the same call fetches again.
    manager._last_sync = 0.0
    manager.sync()
    skill = next(
        s for s in load_filesystem_skills(paths).skills if s.name == "dns-debug"
    )
    assert "new steps" in skill.content


def test_authenticated_repos_must_use_https():
    with pytest.raises(ValueError, match="https"):
        GitSkillRepo(url="http://git.internal/acme/skills.git", token_env="TOK")
    with pytest.raises(ValueError, match="https"):
        _github_app_repo(url="http://github.com/acme/skills.git")
    with pytest.raises(ValueError, match="github_api_url"):
        _github_app_repo(github_api_url="http://ghe.internal/api/v3")
    # Without credentials there is nothing to leak; plain http and file stay allowed.
    assert GitSkillRepo(url="http://git.internal/acme/skills.git").url
    assert GitSkillRepo(url="file:///tmp/repo").url


# ── review follow-ups: leak scrubbing, bounded object store, prune wedge ──


def test_fetch_failure_never_leaks_the_token(tmp_path: Path, monkeypatch):
    """A failed authenticated fetch must not put the credential in the error.

    The credential now travels in env rather than in the URL, so git's stderr
    has nothing to echo -- but _run_git also truncates the command and strips
    any `user:pass@` userinfo, and this pins all three so a future refactor
    that reintroduces URL auth cannot quietly start logging tokens.
    """
    monkeypatch.setenv("LEAK_TOKEN", "ghp_do_not_log_me")
    # A syntactically valid https url that cannot be reached.
    repo = GitSkillRepo(
        url="https://127.0.0.1:1/acme/skills.git",
        name="leaky",
        token_env="LEAK_TOKEN",
    )
    manager = GitSkillRepoManager([repo], root_dir=tmp_path / "checkouts")

    manager.sync()

    error = manager.last_errors["leaky"]
    assert error, "the unreachable fetch should have been recorded"
    assert "ghp_do_not_log_me" not in error
    assert base64.b64encode(b"oauth2:ghp_do_not_log_me").decode() not in error


def test_gc_bounds_the_object_store_across_many_pushes(tmp_path: Path):
    """Superseded commits' objects are dropped, so the store does not grow forever.

    Without the gc after a flip, every push left its objects behind in the bare
    repo -- unbounded growth inside the Helm deployment's /tmp emptyDir, which
    the kubelet enforces by evicting the pod.
    """
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "step 0"})
    manager = _manager_for(repo, tmp_path)
    manager.skill_paths()

    git_dir = tmp_path / "checkouts" / "repo" / "git"

    def object_count() -> int:
        return len([p for p in (git_dir / "objects").rglob("*") if p.is_file()])

    # A body big enough that a leaked copy per push is unmistakable.
    for i in range(1, 8):
        _write_skills(repo, {"dns-debug": f"step {i} " + ("x" * 2000)})
        _commit_all(repo, f"push {i}")
        manager._last_sync = 0.0
        manager.sync()

    assert manager.last_errors == {}
    # The active checkout plus the one worktree still in its grace period are
    # the only roots, so the store holds ~2 commits' worth regardless of how
    # many pushes went by. 7 pushes with no gc would leave far more than this.
    # Measured: 7 pushes leave 32 object files without the gc and 5 with it
    # (30 pushes of a 200KB skill: 3.5MB/124 files vs 231KB/5 files), so this
    # threshold fails if the gc is removed rather than just tracking growth.
    assert object_count() <= 15, f"object store grew unbounded: {object_count()} files"
    # And the newest content is what is being served.
    skill = next(
        s
        for s in load_filesystem_skills(manager.skill_paths()).skills
        if s.name == "dns-debug"
    )
    assert "step 7" in skill.content


def test_never_synced_repo_is_omitted_so_mirror_pruning_is_not_wedged(tmp_path: Path):
    """A repo that never produced a checkout must not appear in skill_paths.

    An unreadable path makes load_filesystem_skills report sources_ok=False,
    which stops the HolmesCustomSkills mirror from pruning ANY row cluster-wide.
    A permanently broken repo (typo'd sub_path, wrong branch, revoked token)
    would otherwise freeze the mirror forever, for every other source too.
    """
    good = _make_skill_repo(tmp_path / "good", {"dns-debug": "x"})
    manager = GitSkillRepoManager(
        [
            GitSkillRepo(url=f"file://{good}", name="good"),
            GitSkillRepo(url=f"file://{tmp_path}/does-not-exist", name="broken"),
        ],
        root_dir=tmp_path / "checkouts",
    )

    paths = manager.skill_paths()

    assert len(paths) == 1 and "good" in paths[0]
    assert "broken" in manager.last_errors
    assert set(manager.unsynced_repos()) == {"broken"}
    # The load is complete, so the mirror may still prune deleted skills.
    loaded = load_filesystem_skills(paths)
    assert loaded.sources_ok is True
    assert "dns-debug" in {s.name for s in loaded.skills}


def test_repo_that_had_a_checkout_stays_listed_when_a_sync_fails(tmp_path: Path):
    """The transient case keeps the old behaviour: still listed, still serving."""
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "x"})
    manager = _manager_for(repo, tmp_path)
    paths = manager.skill_paths()

    shutil.rmtree(repo)
    manager._last_sync = 0.0
    manager.sync()

    assert manager.skill_paths() == paths
    assert manager.unsynced_repos() == {}
    assert "dns-debug" in {s.name for s in load_filesystem_skills(paths).skills}


def test_display_path_folds_the_worktree_sha_back_to_current(tmp_path: Path):
    """The UI should show a stable path, not one carrying the commit sha."""
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "x"})
    manager = _manager_for(repo, tmp_path)
    loaded = load_filesystem_skills(manager.skill_paths())
    skill = next(s for s in loaded.skills if s.name == "dns-debug")

    assert "worktrees" in str(skill.source_path)
    shown = manager.display_path(skill.source_path)
    assert shown == str(
        tmp_path / "checkouts" / "repo" / "current" / "dns-debug" / "SKILL.md"
    )
    # Unrelated paths pass through untouched.
    assert manager.display_path("/etc/holmes/skills/x/SKILL.md") == (
        "/etc/holmes/skills/x/SKILL.md"
    )


def test_ssh_urls_are_rejected_with_an_actionable_message():
    with pytest.raises(ValueError, match="SSH is not supported"):
        GitSkillRepo(url="git@github.com:acme/skills.git")
    with pytest.raises(ValueError, match="ssh://, which is not supported"):
        GitSkillRepo(url="ssh://git@github.com/acme/skills.git")


def test_unsupported_url_scheme_is_rejected():
    with pytest.raises(ValueError, match="scheme"):
        GitSkillRepo(url="javascript://evil/x")


def test_malformed_skill_repos_env_does_not_log_the_credential(monkeypatch, caplog):
    """The url-with-credentials check must not itself log the credential.

    pydantic renders the rejected input into its error string, so the generic
    logging.exception path printed the very token the validator refused.
    """
    monkeypatch.setenv(
        SKILL_REPOS_ENV,
        '[{"url": "https://oauth2:ghp_leaked@github.com/acme/s.git"}]',
    )

    with caplog.at_level("ERROR"):
        assert parse_skill_repos_env() == []

    assert "ghp_leaked" not in caplog.text
    assert "must not embed credentials" in caplog.text


def test_credential_env_appends_to_ambient_git_config(monkeypatch):
    """GIT_CONFIG_* is a shared numbered list -- do not overwrite entry 0.

    Corporate proxies, CA paths and url.insteadOf rewrites are commonly injected
    this way. Writing KEY_0 with COUNT=1 would drop every one of them, and the
    resulting fetch failure looks like a network fault rather than a config one.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.proxy")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "http://corp-proxy:3128")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "url.https://mirror/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "https://github.com/")
    monkeypatch.setenv("SKILL_TOKEN", "tok")
    repo = GitSkillRepo(
        url="https://github.com/acme/skills.git", token_env="SKILL_TOKEN"
    )

    env = repo.credential_env()

    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_2"].endswith(".extraHeader")
    assert "GIT_CONFIG_KEY_0" not in env and "GIT_CONFIG_VALUE_0" not in env
    # And the merged env _run_git builds keeps the ambient entries intact.
    merged = {**os.environ, **env}
    assert merged["GIT_CONFIG_KEY_0"] == "http.proxy"
    assert merged["GIT_CONFIG_KEY_1"] == "url.https://mirror/.insteadOf"


def test_credential_env_tolerates_a_broken_ambient_count(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "not-a-number")
    monkeypatch.setenv("SKILL_TOKEN", "tok")
    repo = GitSkillRepo(
        url="https://github.com/acme/skills.git", token_env="SKILL_TOKEN"
    )

    env = repo.credential_env()

    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"].endswith(".extraHeader")


def test_relative_root_dir_still_publishes_a_working_checkout(tmp_path, monkeypatch):
    """A relative root_dir must not produce a dangling `current` symlink.

    A relative symlink target is resolved against the LINK's directory, not the
    process cwd, so `current` pointed at <root>/<name>/<root>/<name>/worktrees/...
    -- a path that does not exist. The load then reported sources_ok=False, which
    also holds off HolmesCustomSkills pruning. Reachable via a relative
    SKILL_REPOS_DIR.
    """
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "check coredns"})
    monkeypatch.chdir(tmp_path)
    manager = GitSkillRepoManager(
        [GitSkillRepo(url=f"file://{repo}")], root_dir=Path("relative-checkouts")
    )

    paths = manager.skill_paths()

    assert manager.last_errors == {}
    current = tmp_path / "relative-checkouts" / "repo" / CURRENT_LINK_NAME
    assert current.is_symlink()
    assert Path(os.readlink(current)).is_absolute()
    assert current.exists(), "the published symlink must resolve"
    loaded = load_filesystem_skills(paths)
    assert loaded.sources_ok is True
    assert "dns-debug" in {s.name for s in loaded.skills}
