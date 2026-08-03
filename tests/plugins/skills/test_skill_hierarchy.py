"""Tests for the personal skill tier and the per-account name-collision hierarchy."""

from pathlib import Path
from unittest.mock import MagicMock

from holmes.plugins.skills import RobustaSkillInstruction
from holmes.plugins.skills.skill_loader import (
    DEFAULT_HIERARCHY_ORDER,
    Skill,
    _resolve_name_collisions,
    SkillHierarchyConfig,
    SkillSource,
    load_skill_catalog,
)

SKILL_BODY = "---\ndescription: Test skill {name}\n---\n## Goal\nTest\n"


def _write_skill(dir_path: Path, name: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text(SKILL_BODY.format(name=name))


def _dal(global_skills=None, personal_skills=None):
    """A stub DAL. Personal skills are keyed by user_id so we can assert scoping."""
    dal = MagicMock()
    dal.get_skill_catalog.return_value = global_skills or []

    def _personal(user_id):
        return (personal_skills or {}).get(user_id, [])

    dal.get_personal_skill_catalog.side_effect = _personal
    return dal


def _instr(id_, title, symptom="when things break"):
    return RobustaSkillInstruction(id=id_, title=title, symptom=symptom)


def _by_source(catalog, source):
    return [s for s in catalog.skills if s.source == source]


# ── personal tier loading / scoping ──


def test_personal_skills_loaded_for_requesting_user(tmp_path):
    dal = _dal(personal_skills={"user-a": [_instr("uuid-a", "my skill")]})

    catalog = load_skill_catalog(dal=dal, user_id="user-a")

    personal = _by_source(catalog, SkillSource.PERSONAL)
    assert [s.name for s in personal] == ["uuid-a"]
    # name stays the UUID (that is what fetch_skill needs); human name is separate
    assert personal[0].display_name == "my skill"


def test_personal_skills_scoped_to_that_user(tmp_path):
    """User B must not receive user A's personal skills."""
    dal = _dal(
        personal_skills={
            "user-a": [_instr("uuid-a", "a skill")],
            "user-b": [_instr("uuid-b", "b skill")],
        }
    )

    catalog_a = load_skill_catalog(dal=dal, user_id="user-a")
    catalog_b = load_skill_catalog(dal=dal, user_id="user-b")

    assert [s.name for s in _by_source(catalog_a, SkillSource.PERSONAL)] == ["uuid-a"]
    assert [s.name for s in _by_source(catalog_b, SkillSource.PERSONAL)] == ["uuid-b"]


def test_no_personal_skills_without_user_id(tmp_path):
    """The server-initiated guardrail: no end-user id => no personal skills at all.

    Covers alert triage, triggered workflows and scheduled prompts.
    """
    dal = _dal(
        global_skills=[_instr("uuid-g", "global skill")],
        personal_skills={"user-a": [_instr("uuid-a", "a skill")]},
    )

    catalog = load_skill_catalog(dal=dal, user_id=None)

    assert _by_source(catalog, SkillSource.PERSONAL) == []
    # the personal read must not even be attempted
    dal.get_personal_skill_catalog.assert_not_called()
    # global skills still load
    assert [s.name for s in _by_source(catalog, SkillSource.REMOTE)] == ["uuid-g"]


def test_dal_user_id_is_never_used_as_fallback(tmp_path):
    """Holmes's own service identity must never be used to scope personal skills."""
    dal = _dal(personal_skills={"holmes-service-user": [_instr("uuid-s", "svc skill")]})
    dal.user_id = "holmes-service-user"

    catalog = load_skill_catalog(dal=dal, user_id=None)

    assert catalog is None or _by_source(catalog, SkillSource.PERSONAL) == []
    dal.get_personal_skill_catalog.assert_not_called()


# ── flag OFF (default): no cross-tier dedup ──


def test_flag_off_keeps_all_same_named_tiers(tmp_path):
    """Default behaviour: a same-named global + custom + personal skill all survive."""
    _write_skill(tmp_path / "shared-name", "shared-name")
    dal = _dal(
        global_skills=[_instr("uuid-g", "shared-name")],
        personal_skills={"user-a": [_instr("uuid-p", "shared-name")]},
    )

    catalog = load_skill_catalog(
        dal=dal, custom_skill_paths=[tmp_path], user_id="user-a"
    )

    assert len(_by_source(catalog, SkillSource.USER)) == 1
    assert len(_by_source(catalog, SkillSource.REMOTE)) == 1
    assert len(_by_source(catalog, SkillSource.PERSONAL)) == 1


def test_hierarchy_none_behaves_like_disabled(tmp_path):
    _write_skill(tmp_path / "dup", "dup")
    dal = _dal(global_skills=[_instr("uuid-g", "dup")])

    catalog = load_skill_catalog(dal=dal, custom_skill_paths=[tmp_path], hierarchy=None)

    assert len(catalog.skills) == 2


# ── flag ON: winner-takes-all by configured order ──


def test_flag_on_default_order_global_wins(tmp_path):
    _write_skill(tmp_path / "shared-name", "shared-name")
    dal = _dal(
        global_skills=[_instr("uuid-g", "shared-name")],
        personal_skills={"user-a": [_instr("uuid-p", "shared-name")]},
    )

    catalog = load_skill_catalog(
        dal=dal,
        custom_skill_paths=[tmp_path],
        user_id="user-a",
        hierarchy=SkillHierarchyConfig(enabled=True, order=DEFAULT_HIERARCHY_ORDER),
    )

    assert [s.name for s in catalog.skills] == ["uuid-g"]
    assert catalog.skills[0].source == SkillSource.REMOTE


def test_flag_on_reversed_order_personal_wins(tmp_path):
    _write_skill(tmp_path / "shared-name", "shared-name")
    dal = _dal(
        global_skills=[_instr("uuid-g", "shared-name")],
        personal_skills={"user-a": [_instr("uuid-p", "shared-name")]},
    )

    catalog = load_skill_catalog(
        dal=dal,
        custom_skill_paths=[tmp_path],
        user_id="user-a",
        hierarchy=SkillHierarchyConfig(
            enabled=True, order=["personal", "custom", "global"]
        ),
    )

    assert [s.name for s in catalog.skills] == ["uuid-p"]
    assert catalog.skills[0].source == SkillSource.PERSONAL


def _skill(name, source):
    return Skill(name=name, description="d", content="c", source=source)


def test_builtin_always_loses_a_collision():
    """Builtin is lowest priority even though it is not named in `order`.

    Exercised against _resolve_name_collisions directly rather than through
    load_skill_catalog: a filesystem user skill already overwrites a same-named builtin in
    the name-keyed dict before dedup runs, so that path can never present this collision.
    """
    skills = [
        _skill("shared", SkillSource.BUILTIN),
        _skill("shared", SkillSource.USER),
    ]

    kept = _resolve_name_collisions(skills, DEFAULT_HIERARCHY_ORDER)

    assert [s.source for s in kept] == [SkillSource.USER]


def test_builtin_stays_lowest_under_a_partial_order():
    """With order=["global"], personal and builtin are both unlisted -- builtin must still
    lose. Ranking every unlisted source equally would let insertion order decide."""
    skills = [
        _skill("shared", SkillSource.BUILTIN),
        _skill("shared", SkillSource.PERSONAL),
    ]

    kept = _resolve_name_collisions(skills, ["global"])

    assert [s.source for s in kept] == [SkillSource.PERSONAL]


def test_listed_tier_beats_any_unlisted_tier():
    skills = [
        _skill("shared", SkillSource.USER),
        _skill("shared", SkillSource.REMOTE),
    ]

    kept = _resolve_name_collisions(skills, ["global"])

    assert [s.source for s in kept] == [SkillSource.REMOTE]


def test_flag_on_collision_is_case_and_separator_insensitive(tmp_path):
    """Collision keys are normalized, so "My Skill" and "my-skill" collide."""
    _write_skill(tmp_path / "my-skill", "my-skill")
    dal = _dal(global_skills=[_instr("uuid-g", "My Skill")])

    catalog = load_skill_catalog(
        dal=dal,
        custom_skill_paths=[tmp_path],
        hierarchy=SkillHierarchyConfig(enabled=True),
    )

    assert [s.name for s in catalog.skills] == ["uuid-g"]


def test_flag_on_distinct_names_all_survive(tmp_path):
    _write_skill(tmp_path / "alpha", "alpha")
    dal = _dal(
        global_skills=[_instr("uuid-g", "beta")],
        personal_skills={"user-a": [_instr("uuid-p", "gamma")]},
    )

    catalog = load_skill_catalog(
        dal=dal,
        custom_skill_paths=[tmp_path],
        user_id="user-a",
        hierarchy=SkillHierarchyConfig(enabled=True),
    )

    assert len(catalog.skills) == 3


def test_flag_on_unknown_tier_is_ignored(tmp_path):
    """A malformed order must not crash or drop skills."""
    _write_skill(tmp_path / "dup", "dup")
    dal = _dal(global_skills=[_instr("uuid-g", "dup")])

    catalog = load_skill_catalog(
        dal=dal,
        custom_skill_paths=[tmp_path],
        hierarchy=SkillHierarchyConfig(enabled=True, order=["nonsense", "global"]),
    )

    # global is still ranked, so it wins; nothing blows up
    assert [s.name for s in catalog.skills] == ["uuid-g"]


# ── filter-before-dedup invariant ──


def test_filtered_out_global_does_not_suppress_applicable_personal(tmp_path):
    """A higher-tier skill that does not apply to this request must not shadow a
    lower-tier one that does.

    The DAL applies cluster/agent scoping, so a global skill scoped to another cluster
    never reaches the catalog and therefore cannot win the collision.
    """
    dal = _dal(
        # cluster filtering already excluded the global "shared" skill
        global_skills=[],
        personal_skills={"user-a": [_instr("uuid-p", "shared")]},
    )

    catalog = load_skill_catalog(
        dal=dal,
        user_id="user-a",
        hierarchy=SkillHierarchyConfig(enabled=True, order=DEFAULT_HIERARCHY_ORDER),
    )

    assert [s.name for s in catalog.skills] == ["uuid-p"]
    assert catalog.skills[0].source == SkillSource.PERSONAL
