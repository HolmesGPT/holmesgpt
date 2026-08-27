import subprocess
from pathlib import Path

import pytest

from holmes.plugins.skills.git_skill_repos import (
    SKILL_REPOS_ENV,
    GitSkillRepo,
    GitSkillRepoManager,
    parse_skill_repos_env,
)
from holmes.plugins.skills.skill_loader import load_filesystem_skills

SKILL_BODY = "---\ndescription: {description}\n---\n## Goal\n{body}\n"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _make_skill_repo(path: Path, skills: dict[str, str], sub_path: str = "") -> Path:
    """Create a real git repo whose working tree holds SKILL.md directories."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _write_skills(path, skills, sub_path)
    _git(path, "add", "-A")
    _git(path, "commit", "--quiet", "-m", "initial skills")
    return path


def _write_skills(repo: Path, skills: dict[str, str], sub_path: str = "") -> None:
    base = repo / sub_path if sub_path else repo
    for name, body in skills.items():
        skill_dir = base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            SKILL_BODY.format(description=f"Skill {name}", body=body)
        )


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", message)


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

    import shutil

    shutil.rmtree(repo / "oom")
    _commit_all(repo, "drop oom skill")
    manager.sync()

    loaded = load_filesystem_skills(paths)
    assert "oom" not in {s.name for s in loaded.skills}
    # Only the active commit's worktree remains.
    worktrees = list((tmp_path / "checkouts" / "repo" / "worktrees").iterdir())
    assert len(worktrees) == 1


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
    import shutil

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
