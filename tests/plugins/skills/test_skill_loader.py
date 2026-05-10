import os
from pathlib import Path

from holmes.plugins.skills.skill_loader import SkillSource, scan_skill_directory


SKILL_BODY = (
    "---\n"
    "description: Test skill {name}\n"
    "---\n"
    "## Goal\n"
    "Test\n"
)


def _write_skill(dir_path: Path, name: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text(SKILL_BODY.format(name=name))


def test_scan_skill_directory_simple_layout(tmp_path: Path):
    _write_skill(tmp_path / "alpha", "alpha")
    _write_skill(tmp_path / "beta", "beta")

    skills = scan_skill_directory(tmp_path, source=SkillSource.USER)

    assert sorted(s.name for s in skills) == ["alpha", "beta"]


def test_scan_skill_directory_kubernetes_configmap_layout(tmp_path: Path):
    """Reproduce K8s ConfigMap subPath projection.

    Kubernetes mounts ConfigMaps with this layout:

        <mount>/
        ├── ..2026_05_10/                    (real dir, atomic update target)
        │   ├── alpha/SKILL.md
        │   └── beta/SKILL.md
        ├── ..data -> ..2026_05_10           (symlink, swapped on update)
        ├── alpha -> ..data/alpha            (per-key symlinks)
        └── beta  -> ..data/beta

    `os.walk` with default followlinks=False misses the per-key symlinks,
    and the real SKILL.md ends up at depth 2 inside `..2026.../<name>/`,
    which the depth guard skips. The fix needs to (a) follow symlinks and
    (b) compute depth on the walked path, not the resolved path.
    """
    timestamped_dir = tmp_path / "..2026_05_10_10_54_17"
    _write_skill(timestamped_dir / "alpha", "alpha")
    _write_skill(timestamped_dir / "beta", "beta")

    # ..data -> ..2026_05_10_10_54_17
    os.symlink(timestamped_dir.name, tmp_path / "..data")
    # alpha -> ..data/alpha, beta -> ..data/beta
    os.symlink("..data/alpha", tmp_path / "alpha")
    os.symlink("..data/beta", tmp_path / "beta")

    skills = scan_skill_directory(tmp_path, source=SkillSource.USER)

    # Each skill must appear exactly once even though it is reachable via
    # `<name>/SKILL.md` AND `..data/<name>/SKILL.md`.
    names = sorted(s.name for s in skills)
    assert names == ["alpha", "beta"]


def test_scan_skill_directory_missing_dir(tmp_path: Path):
    skills = scan_skill_directory(tmp_path / "does-not-exist")
    assert skills == []


def test_scan_skill_directory_respects_max_depth(tmp_path: Path):
    # SKILL.md at depth 3 should be ignored with default max_depth=2.
    _write_skill(tmp_path / "a" / "b" / "c", "deep")

    skills = scan_skill_directory(tmp_path, source=SkillSource.USER)
    assert skills == []
