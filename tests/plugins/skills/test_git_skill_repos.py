import shutil
from pathlib import Path

import jwt
import pytest
import responses as responses_lib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from holmes.plugins.skills.git_skill_repos import (
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

    _write_skills(repo, {"dns-debug": "check coredns AND kube-proxy", "oom": "check limits"})
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
    assert {s.name for s in load_filesystem_skills(paths).skills} >= {"dns-debug", "oom"}

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
    repo = _make_skill_repo(tmp_path / "repo", {"dns-debug": "x"})
    manager = _manager_for(repo, tmp_path, token_env="MISSING_SKILL_TOKEN")

    manager.sync()

    assert "MISSING_SKILL_TOKEN" in manager.last_errors["repo"]


def test_token_env_injected_into_fetch_url(monkeypatch):
    monkeypatch.setenv("SKILL_TOKEN", "s3cr3t/+")
    repo = GitSkillRepo(url="https://github.com/acme/skills.git", token_env="SKILL_TOKEN")

    authed = repo.authenticated_url()

    assert authed.startswith("https://oauth2:s3cr3t%2F%2B@github.com/")
    # The stored URL never carries the credential.
    assert "s3cr3t" not in repo.url


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

    authed = repo.authenticated_url()

    assert authed == "https://x-access-token:ghs_minted@github.com/acme/skills.git"
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

    first = repo.authenticated_url()
    second = repo.authenticated_url()

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
    skill = next(s for s in load_filesystem_skills(paths).skills if s.name == "dns-debug")
    assert "new steps" not in skill.content

    # ...and once it elapses the same call fetches again.
    manager._last_sync = 0.0
    manager.sync()
    skill = next(s for s in load_filesystem_skills(paths).skills if s.name == "dns-debug")
    assert "new steps" in skill.content
