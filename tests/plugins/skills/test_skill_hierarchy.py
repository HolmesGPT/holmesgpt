"""Tests for the personal skill tier and the per-account name-collision hierarchy."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from holmes.plugins.skills import RobustaSkillInstruction
from holmes.plugins.skills.skill_loader import (
    DEFAULT_HIERARCHY_ORDER,
    TIER_CUSTOM,
    TIER_GLOBAL,
    TIER_PERSONAL,
    TIER_TO_SOURCE,
    Skill,
    _resolve_name_collisions,
    SkillHierarchyConfig,
    SkillSource,
    load_skill_catalog,
    normalize_skill_name,
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
    assert personal[0].title == "my skill"


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


# ── collision key construction ──
#
# Every collision above is decided by comparing normalized human names, so these two
# functions are the foundation the whole hierarchy rests on. They were previously only
# exercised indirectly, through a single case/separator collision test.


class TestNormalizeSkillName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("MySkill", "myskill"),
            ("my skill", "my-skill"),
            ("my_skill", "my-skill"),
            ("My Skill", "my-skill"),
            ("my-skill", "my-skill"),
            ("  my skill  ", "my-skill"),
            ("my\tskill", "my-skill"),
            ("my\nskill", "my-skill"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_skill_name(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["my   skill", "my___skill", "my _ skill", "my \t skill"],
    )
    def test_runs_of_separators_collapse_to_one_hyphen(self, raw):
        r"""`[\s_]+` is a run, not a single character.

        Without the `+` these would become "my---skill" and would NOT collide with
        "my-skill", so the dedup would silently stop working for names typed with
        inconsistent spacing -- exactly the case the hierarchy exists to catch.
        """
        assert normalize_skill_name(raw) == "my-skill"

    def test_existing_hyphens_are_not_collapsed(self):
        r"""Deliberate asymmetry worth pinning: the pattern is `[\s_]+`, so hyphens are
        left alone. "my--skill" and "my-skill" therefore do NOT collide, while
        "my  skill" and "my-skill" do."""
        assert normalize_skill_name("my--skill") == "my--skill"
        assert normalize_skill_name("my--skill") != normalize_skill_name("my-skill")


class TestSkillCollisionKey:
    @staticmethod
    def _skill(name, title=None):
        return Skill(
            name=name,
            description="d",
            content="c",
            source=SkillSource.REMOTE,
            title=title,
        )

    def test_prefers_title(self):
        """Remote and personal skills carry a UUID in `name`, so comparing `name` would
        never detect a collision -- the human name lives in title."""
        skill = self._skill("2c4e4549-a6f4-25b3-c845-ddb4a6f425b3", "My Skill")

        assert skill.collision_key() == "my-skill"

    def test_falls_back_to_name_when_title_is_none(self):
        """Filesystem skills leave title unset because `name` IS the human name."""
        assert self._skill("My Skill").collision_key() == "my-skill"

    def test_falls_back_to_name_when_title_is_empty(self):
        """`title or name` -- "" is falsy, so an empty title falls back to `name`
        rather than collapsing every such skill onto a shared "" key, which would make
        them all collide with each other."""
        skill = self._skill("real-name", title="")

        assert skill.collision_key() == "real-name"

    def test_title_is_normalized_too(self):
        assert self._skill("uuid", "  My_Skill  ").collision_key() == "my-skill"


# ── tier/order configuration edge cases ──


def test_every_default_order_tier_maps_to_a_source():
    """A tier named in the default order but missing from TIER_TO_SOURCE is silently
    ignored by _resolve_name_collisions (it only logs a warning), so the default order
    would quietly stop ranking that tier. Fail loudly here instead."""
    assert [TIER_GLOBAL, TIER_CUSTOM, TIER_PERSONAL] == DEFAULT_HIERARCHY_ORDER
    assert all(tier in TIER_TO_SOURCE for tier in DEFAULT_HIERARCHY_ORDER)
    # Distinct sources, otherwise two tiers would tie and ordering would be ambiguous
    mapped = [TIER_TO_SOURCE[tier] for tier in DEFAULT_HIERARCHY_ORDER]
    assert len(set(mapped)) == len(mapped)


def test_empty_order_through_load_skill_catalog_uses_the_default(tmp_path):
    """`hierarchy.order or DEFAULT_HIERARCHY_ORDER` -- an explicitly empty order is
    treated as unset, so global still wins rather than the tie-break deciding."""
    _write_skill(tmp_path / "dup", "dup")
    dal = _dal(global_skills=[_instr("uuid-g", "dup")])

    catalog = load_skill_catalog(
        dal=dal,
        custom_skill_paths=[tmp_path],
        hierarchy=SkillHierarchyConfig(enabled=True, order=[]),
    )

    assert [s.name for s in catalog.skills] == ["uuid-g"]


def test_empty_order_passed_directly_still_keeps_builtin_lowest():
    """Called directly with [], every non-builtin ties as "unlisted" -- but builtin must
    still rank below even that, so it loses rather than winning on insertion order."""
    skills = [
        _skill("shared", SkillSource.BUILTIN),
        _skill("shared", SkillSource.PERSONAL),
    ]

    kept = _resolve_name_collisions(skills, [])

    assert [s.source for s in kept] == [SkillSource.PERSONAL]


def test_duplicate_tier_in_order_uses_its_first_position():
    """`rank_by_source.setdefault` means a repeated tier keeps its earliest rank, so a
    duplicate cannot demote a tier below one listed after it."""
    skills = [
        _skill("shared", SkillSource.PERSONAL),
        _skill("shared", SkillSource.REMOTE),
    ]

    assert [s.source for s in _resolve_name_collisions(
        skills, ["global", "global", "personal"]
    )] == [SkillSource.REMOTE]
    assert [s.source for s in _resolve_name_collisions(
        skills, ["personal", "global", "personal"]
    )] == [SkillSource.PERSONAL]


def test_survivors_keep_their_input_order():
    """Resolution filters in place rather than rebuilding from the winners dict, so the
    prompt catalog's ordering stays stable instead of reshuffling per request."""
    skills = [
        _skill("alpha", SkillSource.USER),
        _skill("shared", SkillSource.REMOTE),
        _skill("beta", SkillSource.USER),
        _skill("shared", SkillSource.PERSONAL),
    ]

    kept = _resolve_name_collisions(skills, DEFAULT_HIERARCHY_ORDER)

    assert [s.name for s in kept] == ["alpha", "shared", "beta"]
    assert kept[1].source == SkillSource.REMOTE
