import os
from pathlib import Path

from holmes.plugins.skills import RobustaSkillInstruction
from holmes.plugins.skills.skill_loader import (
    SkillSource,
    load_filesystem_skills,
    load_skill_catalog,
    map_robusta_instruction_to_skill,
    scan_skill_directory,
)


SKILL_BODY = "---\n" "description: Test skill {name}\n" "---\n" "## Goal\n" "Test\n"


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


def _deny_scandir_under(root: Path, monkeypatch):
    """Make os.scandir raise PermissionError for paths under `root`, leaving others alone.

    Portable stand-in for a chmod 000 directory: chmod is a no-op for the owner on Windows
    and ineffective as root, which is how CI runs.
    """
    real_scandir = os.scandir

    def deny(path, *args, **kwargs):
        if str(path).startswith(str(root)):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", deny)


def test_scan_skill_directory_reports_unreadable_directory(tmp_path: Path, monkeypatch):
    """An existing but UNREADABLE directory must be reported as a problem.

    os.walk swallows traversal errors unless an `onerror` handler is passed, and `is_dir()`
    succeeds for a directory you cannot read into. Without onerror this returns [] with no
    problem recorded -- indistinguishable from "readable and genuinely empty", which is
    exactly the distinction the mirror prunes on.
    """
    _deny_scandir_under(tmp_path, monkeypatch)
    problems: list[str] = []

    skills = scan_skill_directory(tmp_path, problems=problems)

    assert skills == []
    assert problems, "an unreadable directory must be recorded as a problem"


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


def test_map_robusta_instruction_to_skill_carries_title() -> None:
    """Remote skills keep the UUID as their name but carry the human-readable
    title so chat surfaces can display it (see SkillsFetcher._format_skill_result)."""
    instr = RobustaSkillInstruction(
        id="3e0f2f6a-9f0e-4d5f-8f7a-2b1c9d8e7f6a",
        title="Erlang Debugging",
        symptom="BEAM VM crashes",
        instruction="## Steps\n1. Check BEAM memory",
    )

    skill = map_robusta_instruction_to_skill(instr)

    assert skill.title == "Erlang Debugging"
    assert skill.name == instr.id
    assert skill.source == SkillSource.REMOTE
    assert skill.description == "Erlang Debugging — BEAM VM crashes"
    assert skill.content == instr.instruction


def test_map_robusta_instruction_without_symptom_keeps_title() -> None:
    instr = RobustaSkillInstruction(
        id="3e0f2f6a-9f0e-4d5f-8f7a-2b1c9d8e7f6a",
        title="Erlang Debugging",
        symptom="",
        instruction="## Steps",
    )

    skill = map_robusta_instruction_to_skill(instr)

    assert skill.title == "Erlang Debugging"
    assert skill.description == "Erlang Debugging"


class TestLoadFilesystemSkills:
    """Tests for the load-health signal the HolmesCustomSkills mirror prunes on.

    An empty skill list is ambiguous on its own: it means either "the user deleted their
    last skill" or "nothing could be read". The mirror deletes rows based on what loaded, so
    conflating the two either leaves a stale skill visible forever or wipes the UI's view on
    a transient mount failure. `sources_ok` is what separates them.
    """

    def test_clean_load_reports_ok(self, tmp_path: Path):
        _write_skill(tmp_path / "alpha", "alpha")

        loaded = load_filesystem_skills(custom_skill_paths=[tmp_path])

        assert loaded.sources_ok is True
        assert [s.name for s in loaded.skills] == ["alpha"]

    def test_readable_but_empty_directory_reports_ok(self, tmp_path: Path):
        """The case that must prune: the directory is fine, it just has no skills left."""
        empty = tmp_path / "empty"
        empty.mkdir()

        loaded = load_filesystem_skills(custom_skill_paths=[empty])

        assert loaded.sources_ok is True
        assert loaded.skills == []

    def test_missing_directory_reports_not_ok(self, tmp_path: Path):
        loaded = load_filesystem_skills(
            custom_skill_paths=[tmp_path / "does-not-exist"]
        )

        assert loaded.sources_ok is False
        assert loaded.skills == []

    def test_unparseable_skill_is_named_and_does_not_block_pruning(self, tmp_path: Path):
        """A malformed SKILL.md is a KNOWN failure: we can say exactly which skill is broken.

        So it is reported as a named problem the caller can surface as a row, and it must
        NOT mark the load incomplete -- otherwise one bad file would suppress pruning for
        the whole cluster and an unrelated deletion would never be reflected.
        """
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "SKILL.md").write_text("no frontmatter here")

        loaded = load_filesystem_skills(custom_skill_paths=[tmp_path])

        assert loaded.skills == []
        assert loaded.sources_ok is True
        assert [p.skill_name for p in loaded.failed_skills] == ["broken"]
        assert loaded.failed_skills[0].source == SkillSource.USER
        assert "frontmatter" in loaded.failed_skills[0].error

    def test_unnamed_problems_are_not_reported_as_failed_skills(self, tmp_path: Path):
        """The complement: an unreadable path cannot be attributed to a skill, so it blocks
        pruning and must not produce a row claiming some skill failed."""
        loaded = load_filesystem_skills(custom_skill_paths=[tmp_path / "does-not-exist"])

        assert loaded.sources_ok is False
        assert loaded.failed_skills == []
        assert loaded.problems and loaded.problems[0].skill_name is None

    def test_unreadable_directory_reports_not_ok(self, tmp_path: Path, monkeypatch):
        """The dangerous case: the path exists so `is_dir()` passes, but it cannot be read.

        If this reported ok, the mirror would prune rows for skills that are still on disk
        and merely unreadable this cycle -- the precise wrongful delete sources_ok exists to
        prevent.
        """
        _deny_scandir_under(tmp_path, monkeypatch)

        loaded = load_filesystem_skills(custom_skill_paths=[tmp_path])

        assert loaded.sources_ok is False
        assert loaded.skills == []

    def test_path_that_is_neither_dir_nor_skill_md_reports_not_ok(self, tmp_path: Path):
        stray = tmp_path / "notes.txt"
        stray.write_text("hello")

        loaded = load_filesystem_skills(custom_skill_paths=[stray])

        assert loaded.sources_ok is False

    def test_partial_failure_still_returns_the_readable_skills(self, tmp_path: Path):
        """A good path still loads, but the UNREADABLE one taints sources_ok, so the caller
        will not prune the skills that path would have provided."""
        good = tmp_path / "good"
        _write_skill(good / "alpha", "alpha")

        loaded = load_filesystem_skills(
            custom_skill_paths=[good, tmp_path / "missing"]
        )

        assert loaded.sources_ok is False
        assert [s.name for s in loaded.skills] == ["alpha"]

    def test_does_not_read_supabase(self, tmp_path: Path):
        """Global and personal skills live in HolmesRunbooks and must not be mirrored."""
        _write_skill(tmp_path / "alpha", "alpha")

        loaded = load_filesystem_skills(custom_skill_paths=[tmp_path])

        assert all(
            s.source in (SkillSource.USER, SkillSource.BUILTIN) for s in loaded.skills
        )
