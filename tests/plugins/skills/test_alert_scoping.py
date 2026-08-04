"""Tests for alert scoping on skills (the `alerts` column on HolmesRunbooks).

The hybrid semantics: a deterministic filter when there IS an alert context, and no filtering
at all otherwise, with the alert names surfaced in the description either way.
"""

from unittest.mock import MagicMock

import pytest

from holmes.plugins.skills import RobustaSkillInstruction
from holmes.plugins.skills.skill_loader import (
    Skill,
    SkillHierarchyConfig,
    SkillSource,
    load_skill_catalog,
    map_robusta_instruction_to_skill,
)

ALERT_A = "E2EAlert/Deployment/checkout-api"
ALERT_B = "KubeCPUOvercommit"


def _dal(global_skills=None, personal_skills=None):
    dal = MagicMock()
    dal.get_skill_catalog.return_value = global_skills or []
    dal.get_personal_skill_catalog.side_effect = lambda user_id: (
        personal_skills or {}
    ).get(user_id, [])
    return dal


def _names(catalog):
    return sorted(s.display_name or s.name for s in (catalog.skills if catalog else []))


class TestAppliesToAlert:
    def test_unscoped_skill_applies_to_every_alert(self):
        s = Skill(name="x", description="d", content="c", source=SkillSource.REMOTE)
        assert s.applies_to_alert(ALERT_A) is True
        assert s.applies_to_alert(None) is True

    def test_scoped_skill_applies_only_to_its_own_alerts(self):
        s = Skill(
            name="x",
            description="d",
            content="c",
            source=SkillSource.REMOTE,
            alerts=[ALERT_A],
        )
        assert s.applies_to_alert(ALERT_A) is True
        assert s.applies_to_alert(ALERT_B) is False

    def test_scoped_skill_still_applies_when_there_is_no_alert_context(self):
        """Ask Holmes chat has no firing alert; hiding alert-scoped skills there would make
        them unreachable from chat entirely."""
        s = Skill(
            name="x",
            description="d",
            content="c",
            source=SkillSource.REMOTE,
            alerts=[ALERT_A],
        )
        assert s.applies_to_alert(None) is True


class TestAlertsReachTheSkill:
    def test_alerts_are_carried_onto_the_skill(self):
        instr = RobustaSkillInstruction(
            id="u1", symptom="pods restart", title="Crashloop", alerts=[ALERT_A]
        )
        assert map_robusta_instruction_to_skill(instr).alerts == [ALERT_A]

    def test_alert_names_are_surfaced_in_the_description(self):
        """The filter only fires with an alert context, so in chat the description is the
        only signal to the model that a skill is alert-specific."""
        instr = RobustaSkillInstruction(
            id="u1", symptom="pods restart", title="Crashloop", alerts=[ALERT_A, ALERT_B]
        )
        desc = map_robusta_instruction_to_skill(instr).description
        assert ALERT_A in desc and ALERT_B in desc

    def test_alert_only_skill_needs_no_symptom(self):
        """The UI validates "either symptoms or alerts", so requiring a symptom dropped
        alert-only skills entirely."""
        instr = RobustaSkillInstruction(id="u1", title="Checkout", alerts=[ALERT_A])
        skill = map_robusta_instruction_to_skill(instr)
        assert skill.alerts == [ALERT_A]
        assert ALERT_A in skill.description


class TestAlertFilteringInTheCatalog:
    def _scoped(self, id_, title, alerts):
        return RobustaSkillInstruction(
            id=id_, symptom="s", title=title, alerts=alerts
        )

    def test_alert_context_drops_skills_scoped_to_other_alerts(self):
        dal = _dal(
            global_skills=[
                self._scoped("1", "for-a", [ALERT_A]),
                self._scoped("2", "for-b", [ALERT_B]),
                RobustaSkillInstruction(id="3", symptom="s", title="unscoped"),
            ]
        )
        catalog = load_skill_catalog(dal=dal, alert_name=ALERT_A)
        assert _names(catalog) == ["for-a", "unscoped"]

    def test_no_alert_context_keeps_every_skill(self):
        dal = _dal(
            global_skills=[
                self._scoped("1", "for-a", [ALERT_A]),
                self._scoped("2", "for-b", [ALERT_B]),
            ]
        )
        catalog = load_skill_catalog(dal=dal, alert_name=None)
        assert _names(catalog) == ["for-a", "for-b"]

    def test_personal_skills_are_alert_filtered_too(self):
        dal = _dal(
            personal_skills={
                "u": [
                    self._scoped("1", "mine-a", [ALERT_A]),
                    self._scoped("2", "mine-b", [ALERT_B]),
                ]
            }
        )
        catalog = load_skill_catalog(dal=dal, user_id="u", alert_name=ALERT_B)
        assert _names(catalog) == ["mine-b"]

    def test_filtering_happens_before_hierarchy_dedup(self):
        """A higher-tier skill scoped to a DIFFERENT alert must not suppress a lower-tier one
        that does apply. Inverting the order would silently drop the skill that should run."""
        same = "shared-name"
        dal = _dal(
            global_skills=[self._scoped("g", same, [ALERT_B])],
            personal_skills={"u": [self._scoped("p", same, [ALERT_A])]},
        )
        catalog = load_skill_catalog(
            dal=dal,
            user_id="u",
            alert_name=ALERT_A,
            hierarchy=SkillHierarchyConfig(enabled=True),  # global > custom > personal
        )
        # The global one does not apply to ALERT_A, so the personal one must survive.
        assert [s.source for s in catalog.skills] == [SkillSource.PERSONAL]

    @pytest.mark.parametrize("alert", [ALERT_A, ALERT_B])
    def test_filesystem_skills_are_never_alert_filtered(self, alert, tmp_path):
        d = tmp_path / "fs-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\ndescription: fs\n---\nbody\n")
        catalog = load_skill_catalog(
            dal=None, custom_skill_paths=[str(tmp_path)], alert_name=alert
        )
        assert _names(catalog) == ["fs-skill"]
