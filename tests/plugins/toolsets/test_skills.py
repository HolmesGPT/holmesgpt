import os
from unittest.mock import Mock

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.skills.skill_loader import Skill, SkillCatalog, SkillSource
from holmes.plugins.toolsets.skills.skills_fetcher import (
    SkillsFetcher,
    SkillsToolset,
)
from tests.conftest import create_mock_tool_invoke_context

TEST_SKILLS_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "skills")


def test_SkillsFetcher_not_found():
    skills_fetch_tool = SkillsFetcher(SkillsToolset())
    result = skills_fetch_tool._invoke(
        {"skill_id": "nonexistent-skill"},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.error is not None


def test_SkillsFetcher_with_skill_catalog():
    catalog = SkillCatalog(
        skills=[
            Skill(
                name="test-skill",
                description="A test skill",
                content="## Steps\n1. Do something",
                source=SkillSource.USER,
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    result = skills_fetch_tool._invoke(
        {"skill_id": "test-skill"},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.error is None
    assert result.data is not None
    assert "Do something" in result.data


def test_SkillsFetcher_empty_id():
    skills_fetch_tool = SkillsFetcher(SkillsToolset())
    result = skills_fetch_tool._invoke(
        {"skill_id": ""},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR


def test_SkillsFetcher_one_liner():
    catalog = SkillCatalog(
        skills=[
            Skill(
                name="test-skill",
                description="A test skill",
                content="content",
                source=SkillSource.USER,
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    assert (
        skills_fetch_tool.get_parameterized_one_liner({"skill_id": "test-skill"})
        == "Skills: Fetch Skill test-skill"
    )


REMOTE_SKILL_UUID = "3e0f2f6a-9f0e-4d5f-8f7a-2b1c9d8e7f6a"


def _mock_remote_dal(title="Erlang Debugging"):
    dal = Mock()
    dal.enabled = True
    skill_content = Mock()
    skill_content.id = REMOTE_SKILL_UUID
    skill_content.title = title
    skill_content.symptom = "BEAM VM crashes"
    skill_content.instruction = "## Steps\n1. Check BEAM memory"
    dal.get_skill_content.return_value = skill_content
    return dal


def test_SkillsFetcher_remote_skill_result_carries_title():
    """Remote skills are fetched by their runbook UUID; the result params must
    carry the human-readable title so chat surfaces (e.g. the Slack skills
    footer) can display it instead of the UUID."""
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), dal=_mock_remote_dal())
    result = skills_fetch_tool._invoke(
        {"skill_id": REMOTE_SKILL_UUID},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.params["skill_id"] == REMOTE_SKILL_UUID
    assert result.params["skill_title"] == "Erlang Debugging"


def test_SkillsFetcher_local_skill_result_has_no_title():
    """Local skills are already fetched by a readable name — no title field."""
    catalog = SkillCatalog(
        skills=[
            Skill(
                name="test-skill",
                description="A test skill",
                content="content",
                source=SkillSource.USER,
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    result = skills_fetch_tool._invoke(
        {"skill_id": "test-skill"},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "skill_title" not in result.params


def test_SkillsFetcher_one_liner_uses_title_for_remote_skills():
    """The tool-call display line shows the title, not the runbook UUID."""
    catalog = SkillCatalog(
        skills=[
            Skill(
                name=REMOTE_SKILL_UUID,
                description="Erlang Debugging — BEAM VM crashes",
                content="",
                source=SkillSource.REMOTE,
                source_path=REMOTE_SKILL_UUID,
                title="Erlang Debugging",
            )
        ]
    )
    skills_fetch_tool = SkillsFetcher(SkillsToolset(), skill_catalog=catalog)
    assert (
        skills_fetch_tool.get_parameterized_one_liner({"skill_id": REMOTE_SKILL_UUID})
        == "Skills: Fetch Skill Erlang Debugging"
    )
