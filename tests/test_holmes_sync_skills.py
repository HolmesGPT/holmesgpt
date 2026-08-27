"""Tests for the HolmesCustomSkills mirror sync.

The mirror deletes rows based on what loaded from disk, which makes the empty case load
bearing: deleting your last custom skill must clear the mirror, but an unreadable
ConfigMap mount must not. These tests pin the decision down at the layer that makes it.
"""

from pathlib import Path
from unittest.mock import Mock

from holmes.utils.holmes_sync_skills import holmes_sync_skills_status

SKILL_BODY = "---\ndescription: Test skill\n---\n## Goal\nTest\n"


def _write_skill(dir_path: Path, name: str) -> None:
    (dir_path / name).mkdir(parents=True, exist_ok=True)
    (dir_path / name / "SKILL.md").write_text(SKILL_BODY)


def _dal() -> Mock:
    dal = Mock()
    dal.account_id = "acct-1"
    return dal


def _config(paths, skill_repos=None, repo_manager=None) -> Mock:
    config = Mock()
    config.cluster_name = "c1"
    config.custom_skill_paths = paths
    # The sync reads the combined view (configured paths + git-repo checkouts).
    config.all_skill_paths = paths
    config.skill_repos = skill_repos or []
    config.skill_repo_manager = repo_manager
    return config


def test_clean_load_syncs_with_prune_enabled(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([tmp_path]))

    rows, cluster = dal.sync_skills.call_args[0]
    assert cluster == "c1"
    assert [r["skill_name"] for r in rows] == ["alpha"]
    assert dal.sync_skills.call_args[1]["prune"] is True


def test_last_skill_deleted_still_prunes(tmp_path: Path):
    """The reported gap: an empty but READABLE directory must prune the mirror.

    Previously this returned early without calling sync_skills at all, so the row for the
    deleted skill stayed in HolmesCustomSkills forever and the UI kept listing it.
    """
    empty = tmp_path / "skills"
    empty.mkdir()
    dal = _dal()

    holmes_sync_skills_status(dal, _config([empty]))

    dal.sync_skills.assert_called_once()
    rows, _ = dal.sync_skills.call_args[0]
    assert rows == []
    assert dal.sync_skills.call_args[1]["prune"] is True


def test_unreadable_source_does_not_prune(tmp_path: Path):
    """An unmounted ConfigMap looks like an empty one, so it must not wipe the mirror."""
    dal = _dal()

    holmes_sync_skills_status(dal, _config([tmp_path / "not-mounted-yet"]))

    rows, _ = dal.sync_skills.call_args[0]
    assert rows == []
    assert dal.sync_skills.call_args[1]["prune"] is False


def test_partial_failure_upserts_but_does_not_prune(tmp_path: Path):
    """Conservative rule: one bad path suppresses the prune for the whole sync, so the
    skills the failed path would have provided are not deleted from the mirror."""
    good = tmp_path / "good"
    _write_skill(good, "alpha")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([good, tmp_path / "missing"]))

    rows, _ = dal.sync_skills.call_args[0]
    assert [r["skill_name"] for r in rows] == ["alpha"]
    assert dal.sync_skills.call_args[1]["prune"] is False


def test_missing_cluster_name_skips_entirely(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    dal = _dal()
    config = _config([tmp_path])
    config.cluster_name = None

    holmes_sync_skills_status(dal, config)

    dal.sync_skills.assert_not_called()


def test_sync_failure_never_raises(tmp_path: Path):
    """A display-only mirror must never prevent Holmes from starting."""
    dal = _dal()
    dal.sync_skills.side_effect = RuntimeError("boom")

    _write_skill(tmp_path, "alpha")
    holmes_sync_skills_status(dal, _config([tmp_path]))

    # Assert the call happened, otherwise the side_effect never fires and this passes
    # vacuously -- it would still be green if the sync were skipped entirely, proving
    # nothing about suppression.
    dal.sync_skills.assert_called_once()


def test_loader_failure_never_raises_and_skips_the_write(monkeypatch, tmp_path: Path):
    """The loader runs BEFORE the rows are built, so its failure is a separate path.

    Must not raise, and must not reach sync_skills at all -- with no loaded skills and no
    health signal there is nothing to upsert and pruning would be a guess.
    """
    dal = _dal()

    def boom(*_args, **_kwargs):
        raise RuntimeError("loader exploded")

    monkeypatch.setattr(
        "holmes.utils.holmes_sync_skills.load_filesystem_skills", boom
    )

    holmes_sync_skills_status(dal, _config([tmp_path]))

    dal.sync_skills.assert_not_called()


def _broken_skill(dir_path: Path, name: str) -> None:
    (dir_path / name).mkdir(parents=True, exist_ok=True)
    (dir_path / name / "SKILL.md").write_text("no frontmatter here")


def test_malformed_skill_gets_an_error_row(tmp_path: Path):
    """A SKILL.md that fails to parse must appear as broken, not vanish.

    Before this, the loader dropped unparseable skills, so nothing broken ever reached the
    row builder -- status was always "ok" and error always NULL. A user's malformed file
    simply had no effect anywhere in the product, discoverable only in the pod logs.
    """
    _write_skill(tmp_path, "good")
    _broken_skill(tmp_path, "bad")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([tmp_path]))

    rows, _ = dal.sync_skills.call_args[0]
    by_name = {r["skill_name"]: r for r in rows}

    assert by_name["good"]["status"] == "ok"
    assert by_name["good"]["error"] is None

    bad = by_name["bad"]
    assert bad["status"] == "error"
    assert "frontmatter" in bad["error"]
    assert bad["source"] == "custom"
    assert bad["source_path"] is not None
    # Nothing trustworthy to report -- the parse that would produce them is what failed.
    assert bad["description"] is None
    assert bad["content"] is None


def test_malformed_skill_does_not_suppress_pruning(tmp_path: Path):
    """A parse failure is a KNOWN state, now represented as a row, so it must not block the
    prune. Otherwise one malformed file would freeze the mirror for the whole cluster and
    deleting an unrelated skill would leave its row behind forever."""
    _write_skill(tmp_path, "good")
    _broken_skill(tmp_path, "bad")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([tmp_path]))

    assert dal.sync_skills.call_args[1]["prune"] is True


def test_unreadable_source_still_suppresses_pruning(tmp_path: Path):
    """The other half of the distinction: an unreadable path tells us nothing about what
    skills should exist, so the load is not authoritative enough to delete from."""
    _write_skill(tmp_path, "good")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([tmp_path, tmp_path / "not-mounted"]))

    assert dal.sync_skills.call_args[1]["prune"] is False


def test_error_row_keeps_the_skill_from_being_pruned(tmp_path: Path):
    """The failed skill is in the provided names, so the prune that follows will not delete
    the very row that reports the failure."""
    _broken_skill(tmp_path, "bad")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([tmp_path]))

    rows, _ = dal.sync_skills.call_args[0]
    assert [r["skill_name"] for r in rows] == ["bad"]
    assert dal.sync_skills.call_args[1]["prune"] is True


def test_rows_are_unique_by_skill_name(tmp_path: Path):
    """The batch upsert conflicts on (account_id, cluster_id, skill_name), and PostgreSQL
    refuses an ON CONFLICT DO UPDATE that touches the same row twice -- it raises a
    cardinality violation. So two rows sharing a skill_name do not resolve last-write-wins;
    they abort the whole statement. sync_skills catches that, and because the prune runs
    after the upsert in the same try block, ONE name collision silently kills the entire
    mirror sync for the cluster rather than just the colliding row.

    Reachable whenever two custom_skill_paths hold the same directory name and one of them
    is malformed.
    """
    good_path = tmp_path / "a"
    bad_path = tmp_path / "b"
    _write_skill(good_path, "shared")
    _broken_skill(bad_path, "shared")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([good_path, bad_path]))

    rows, _ = dal.sync_skills.call_args[0]
    keys = [(r["account_id"], r["cluster_id"], r["skill_name"]) for r in rows]
    assert len(keys) == len(set(keys)), f"duplicate upsert keys: {keys}"


def test_failure_row_wins_over_a_same_named_healthy_skill(tmp_path: Path):
    """When they collide the error must survive, not be masked by the healthy row.

    A broken skill the user cannot see is the whole problem this feature exists to fix, so
    silently preferring the row that parsed would defeat it.
    """
    good_path = tmp_path / "a"
    bad_path = tmp_path / "b"
    _write_skill(good_path, "shared")
    _broken_skill(bad_path, "shared")
    dal = _dal()

    holmes_sync_skills_status(dal, _config([good_path, bad_path]))

    rows, _ = dal.sync_skills.call_args[0]
    shared = [r for r in rows if r["skill_name"] == "shared"]
    assert len(shared) == 1
    assert shared[0]["status"] == "error"
    assert "frontmatter" in shared[0]["error"]


def test_git_repo_skills_are_labeled_with_their_repo_url(tmp_path: Path):
    """Skills whose files come from a synced git repo report source "git:<url>".

    The UI parses that prefix to show which repo a skill syncs from; everything
    else keeps the plain "custom"/"builtin" labels.
    """
    import subprocess

    from holmes.plugins.skills.git_skill_repos import GitSkillRepo, GitSkillRepoManager

    repo_dir = tmp_path / "repo"
    _write_skill(repo_dir, "from-git")
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "skills"], cwd=repo_dir, check=True)

    plain_dir = tmp_path / "plain"
    _write_skill(plain_dir, "from-files")

    repo = GitSkillRepo(url=f"file://{repo_dir}")
    manager = GitSkillRepoManager([repo], root_dir=tmp_path / "checkouts")
    dal = _dal()
    config = _config(
        [plain_dir] + manager.skill_paths(),
        skill_repos=[repo],
        repo_manager=manager,
    )

    holmes_sync_skills_status(dal, config)

    rows, _ = dal.sync_skills.call_args[0]
    by_name = {r["skill_name"]: r for r in rows}
    assert by_name["from-git"]["source"] == f"git:file://{repo_dir}"
    assert by_name["from-files"]["source"] == "custom"
