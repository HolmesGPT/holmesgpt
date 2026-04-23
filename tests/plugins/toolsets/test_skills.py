import os

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.skills.skills_fetcher import (
    SkillsFetcher,
    SkillsToolset,
)
from tests.conftest import create_mock_tool_invoke_context

TEST_SKILLS_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "skills"
)


def test_SkillsFetcher():
    skills_fetch_tool = SkillsFetcher(SkillsToolset(dal=None))
    result = skills_fetch_tool._invoke(
        {"runbook_id": "wrong_runbook_path.md", "type": "md_file"},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.error is not None


def test_SkillsFetcher_with_additional_search_paths():
    skills_fetch_tool = SkillsFetcher(
        SkillsToolset(dal=None, additional_search_paths=[TEST_SKILLS_PATH]),
        additional_search_paths=[TEST_SKILLS_PATH],
    )
    result = skills_fetch_tool._invoke(
        {
            "runbook_id": "test_runbook.md",
            "type": "md_file",
        },
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.error is None
    assert result.data is not None
    assert (
        skills_fetch_tool.get_parameterized_one_liner(
            {
                "runbook_id": "test_runbook.md",
                "type": "md_file",
            }
        )
        == "Skills: Fetch Runbook test_runbook.md"
    )
