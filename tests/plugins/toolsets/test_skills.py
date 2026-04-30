import os

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.skills.skill_loader import Skill, SkillCatalog, SkillSource
from holmes.plugins.toolsets.skills.skills_fetcher import (
    SkillsFetcher,
    SkillsToolset,
)
from tests.conftest import create_mock_tool_invoke_context

TEST_SKILLS_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "skills"
)


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
