import os
from pathlib import Path

from holmes.plugins.skills.skill_loader import (
    SkillSource,
    load_skill_catalog,
    scan_skill_directory,
)


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


def test_load_skill_catalog_merges_multiple_custom_paths(tmp_path: Path):
    """Skills from every entry in custom_skill_paths should be aggregated.

    This is what the helm `customSkillPaths` list relies on — the chart joins
    entries with commas into CUSTOM_SKILL_PATHS, the Python side splits them,
    and load_skill_catalog must load skills from each directory.
    """
    path_a = tmp_path / "team-a"
    path_b = tmp_path / "team-b"
    path_c = tmp_path / "team-c"
    _write_skill(path_a / "alpha", "alpha")
    _write_skill(path_b / "beta", "beta")
    _write_skill(path_c / "gamma", "gamma")

    catalog = load_skill_catalog(custom_skill_paths=[path_a, path_b, path_c])

    assert catalog is not None
    user_skill_names = sorted(
        s.name for s in catalog.skills if s.source == SkillSource.USER
    )
    assert user_skill_names == ["alpha", "beta", "gamma"]


def test_load_skill_catalog_mixed_dir_and_skill_file(tmp_path: Path):
    """Each custom_skill_paths entry can be a directory OR a single SKILL.md file."""
    dir_path = tmp_path / "dir-skills"
    _write_skill(dir_path / "alpha", "alpha")

    single_file_dir = tmp_path / "loose"
    _write_skill(single_file_dir, "loose-skill")
    single_skill_file = single_file_dir / "SKILL.md"

    catalog = load_skill_catalog(custom_skill_paths=[dir_path, single_skill_file])

    assert catalog is not None
    user_skill_names = sorted(
        s.name for s in catalog.skills if s.source == SkillSource.USER
    )
    assert user_skill_names == ["alpha", "loose"]


def test_load_skill_catalog_later_path_overrides_earlier(tmp_path: Path):
    """When two custom paths define the same skill name, the later one wins."""
    path_a = tmp_path / "a"
    path_b = tmp_path / "b"
    _write_skill(path_a / "shared", "from-a")
    _write_skill(path_b / "shared", "from-b")

    catalog = load_skill_catalog(custom_skill_paths=[path_a, path_b])

    assert catalog is not None
    shared = [s for s in catalog.skills if s.name == "shared"]
    assert len(shared) == 1
    assert shared[0].source_path is not None
    assert str(path_b) in shared[0].source_path
