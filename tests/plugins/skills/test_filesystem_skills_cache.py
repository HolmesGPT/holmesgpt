"""Parsed SKILL.md files are reused across turns until a file under the skill paths
changes (ROB-1554)."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from holmes.plugins.skills import RobustaSkillInstruction, skill_loader
from holmes.plugins.skills.skill_loader import (
    clear_filesystem_skills_cache,
    load_skill_catalog,
)
from holmes.utils.single_flight_cache import SetupTracker

SKILL_BODY = "---\ndescription: {description}\n---\n{content}\n"


def _write_skill(dir_path: Path, name: str, description: str = "", content="Body"):
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / "SKILL.md"
    path.write_text(
        SKILL_BODY.format(description=description or f"Skill {name}", content=content)
    )
    return path


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_filesystem_skills_cache()
    yield
    clear_filesystem_skills_cache()


@pytest.fixture
def parse_spy():
    with patch.object(
        skill_loader, "parse_skill_file", wraps=skill_loader.parse_skill_file
    ) as spy:
        yield spy


def _names(catalog):
    return sorted(s.name for s in catalog.skills) if catalog else []


def _by_name(catalog, name):
    return next(s for s in catalog.skills if s.name == name)


def test_unchanged_skills_are_parsed_once(tmp_path, parse_spy):
    _write_skill(tmp_path / "alpha", "alpha")
    _write_skill(tmp_path / "beta", "beta")

    first = load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    second = load_skill_catalog(custom_skill_paths=[str(tmp_path)])

    assert _names(first) == _names(second) == ["alpha", "beta"]
    assert parse_spy.call_count == 2


def test_editing_a_skill_invalidates_the_parsed_catalog(tmp_path, parse_spy):
    path = _write_skill(tmp_path / "alpha", "alpha", content="old body")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])

    path.write_text(SKILL_BODY.format(description="Skill alpha", content="new body"))
    _bump_mtime(path)
    catalog = load_skill_catalog(custom_skill_paths=[str(tmp_path)])

    assert _by_name(catalog, "alpha").content == "new body"
    assert parse_spy.call_count == 2


def test_bumping_mtime_alone_invalidates(tmp_path, parse_spy):
    path = _write_skill(tmp_path / "alpha", "alpha")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    _bump_mtime(path)
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    assert parse_spy.call_count == 2


def test_same_size_edit_with_identical_mtime_is_caught_by_inode(tmp_path):
    """Atomic writers (ConfigMap updates, editors) replace the file, changing the inode."""
    path = _write_skill(tmp_path / "alpha", "alpha", content="AAAA")
    st = path.stat()
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])

    replacement = tmp_path / "alpha" / "SKILL.md.tmp"
    replacement.write_text(SKILL_BODY.format(description="Skill alpha", content="BBBB"))
    os.utime(replacement, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(replacement, path)

    catalog = load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    assert _by_name(catalog, "alpha").content == "BBBB"


def test_adding_a_skill_is_picked_up(tmp_path):
    _write_skill(tmp_path / "alpha", "alpha")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    _write_skill(tmp_path / "beta", "beta")
    assert _names(load_skill_catalog(custom_skill_paths=[str(tmp_path)])) == [
        "alpha",
        "beta",
    ]


def test_deleting_a_skill_is_picked_up(tmp_path):
    _write_skill(tmp_path / "alpha", "alpha")
    beta = _write_skill(tmp_path / "beta", "beta")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    beta.unlink()
    assert _names(load_skill_catalog(custom_skill_paths=[str(tmp_path)])) == ["alpha"]


def test_symlink_flip_is_picked_up_even_with_identical_stats(tmp_path):
    """Git skill repos and ConfigMap mounts swap a `current` / `..data` symlink to a new
    tree; the new files can have the same size and mtime as the old ones."""
    old_tree = tmp_path / "sha-old"
    new_tree = tmp_path / "sha-new"
    old = _write_skill(old_tree / "alpha", "alpha", content="AAAA")
    new = _write_skill(new_tree / "alpha", "alpha", content="BBBB")
    st = old.stat()
    os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))
    current = tmp_path / "current"
    current.symlink_to(old_tree)

    first = load_skill_catalog(custom_skill_paths=[str(current)])
    tmp_link = tmp_path / "current.tmp"
    tmp_link.symlink_to(new_tree)
    os.replace(tmp_link, current)
    second = load_skill_catalog(custom_skill_paths=[str(current)])

    assert _by_name(first, "alpha").content == "AAAA"
    assert _by_name(second, "alpha").content == "BBBB"


def test_skill_path_that_appears_later_is_picked_up(tmp_path):
    later = tmp_path / "later"
    assert load_skill_catalog(custom_skill_paths=[str(later)]) is None
    _write_skill(later / "alpha", "alpha")
    assert _names(load_skill_catalog(custom_skill_paths=[str(later)])) == ["alpha"]


def test_single_skill_file_path_is_tracked(tmp_path):
    path = _write_skill(tmp_path / "solo", "solo", content="v1")
    load_skill_catalog(custom_skill_paths=[str(path)])
    path.write_text(SKILL_BODY.format(description="Skill solo", content="v2"))
    _bump_mtime(path)
    catalog = load_skill_catalog(custom_skill_paths=[str(path)])
    assert _by_name(catalog, "solo").content == "v2"


def test_fixing_a_broken_skill_is_picked_up(tmp_path):
    path = tmp_path / "alpha" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("no frontmatter")
    assert load_skill_catalog(custom_skill_paths=[str(tmp_path)]) is None
    path.write_text(SKILL_BODY.format(description="Skill alpha", content="fixed"))
    _bump_mtime(path)
    assert _names(load_skill_catalog(custom_skill_paths=[str(tmp_path)])) == ["alpha"]


def test_skills_deeper_than_scan_depth_do_not_affect_the_key(tmp_path, parse_spy):
    _write_skill(tmp_path / "alpha", "alpha")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    _write_skill(tmp_path / "a" / "b" / "c", "too-deep")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    assert parse_spy.call_count == 1


def test_different_paths_are_cached_separately(tmp_path):
    _write_skill(tmp_path / "one" / "alpha", "alpha")
    _write_skill(tmp_path / "two" / "beta", "beta")
    assert _names(load_skill_catalog(custom_skill_paths=[str(tmp_path / "one")])) == [
        "alpha"
    ]
    assert _names(load_skill_catalog(custom_skill_paths=[str(tmp_path / "two")])) == [
        "beta"
    ]


def test_remote_skills_added_by_one_turn_do_not_leak_into_the_next(tmp_path):
    """load_skill_catalog adds Supabase skills into the dict it gets from the cache."""
    _write_skill(tmp_path / "alpha", "alpha")
    dal = MagicMock()
    dal.get_skill_catalog.return_value = [
        RobustaSkillInstruction(id="remote-1", symptom="s", title="Remote")
    ]
    dal.get_personal_skill_catalog.return_value = None
    with_remote = load_skill_catalog(dal=dal, custom_skill_paths=[str(tmp_path)])
    without = load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    assert _names(with_remote) == ["alpha", "remote-1"]
    assert _names(without) == ["alpha"]


def test_clear_forces_reparse(tmp_path, parse_spy):
    _write_skill(tmp_path / "alpha", "alpha")
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    clear_filesystem_skills_cache()
    load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    assert parse_spy.call_count == 2


def test_walk_error_is_part_of_the_key(tmp_path):
    """A directory that cannot be read this turn must not share a key with the same
    directory once it becomes readable."""
    _write_skill(tmp_path / "alpha", "alpha")
    real_walk = os.walk

    def failing_walk(top, followlinks, onerror):
        onerror(PermissionError(13, "Permission denied", str(tmp_path / "beta")))
        return real_walk(top, followlinks=followlinks, onerror=onerror)

    healthy = skill_loader._skill_files_fingerprint([str(tmp_path)])
    with patch.object(skill_loader.os, "walk", side_effect=failing_walk):
        failing = skill_loader._skill_files_fingerprint([str(tmp_path)])
    assert healthy != failing
    assert ("walk-error", str(tmp_path / "beta"), 13) in failing


def test_vanished_skill_file_between_walk_and_stat_is_recorded(tmp_path):
    path = _write_skill(tmp_path / "alpha", "alpha")
    with patch.object(
        skill_loader, "_walk_skill_files", return_value=iter([path.with_name("gone")])
    ):
        fingerprint = skill_loader._skill_files_fingerprint([str(tmp_path)])
    assert (str(path.with_name("gone")), "error", 2) in fingerprint


def test_cache_lookups_are_counted_in_setup_metrics(tmp_path):
    _write_skill(tmp_path / "alpha", "alpha")
    setup = SetupTracker()
    with setup.track():
        load_skill_catalog(custom_skill_paths=[str(tmp_path)])
        load_skill_catalog(custom_skill_paths=[str(tmp_path)])
    assert (setup.stats.hits, setup.stats.misses) == (1, 1)
