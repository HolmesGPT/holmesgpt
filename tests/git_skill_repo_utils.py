"""Shared builder for tests that need a real git repo holding SKILL.md skills."""

import subprocess
from pathlib import Path

SKILL_BODY = "---\ndescription: {description}\n---\n## Goal\n{body}\n"


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def write_skills(repo: Path, skills: dict[str, str], sub_path: str = "") -> None:
    base = repo / sub_path if sub_path else repo
    for name, body in skills.items():
        skill_dir = base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            SKILL_BODY.format(description=f"Skill {name}", body=body)
        )


def commit_all(repo: Path, message: str) -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "--quiet", "-m", message)


def make_skill_repo(path: Path, skills: dict[str, str], sub_path: str = "") -> Path:
    """Create a git repo whose working tree holds the given SKILL.md directories."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "--quiet", "-b", "main")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test")
    write_skills(path, skills, sub_path)
    commit_all(path, "initial skills")
    return path
