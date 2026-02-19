import os

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.runbook.runbook_fetcher import (
    RunbookFetcher,
    RunbookToolset,
)
from tests.conftest import create_mock_tool_invoke_context

TEST_RUNBOOKS_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "runbooks"
)


def test_RunbookFetcher():
    runbook_fetch_tool = RunbookFetcher(RunbookToolset(dal=None))
    result = runbook_fetch_tool._invoke(
        {"runbook_id": "wrong_runbook_path.md", "type": "md_file"},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.error is not None


def test_RunbookFetcher_with_additional_search_paths():
    runbook_fetch_tool = RunbookFetcher(
        RunbookToolset(dal=None, additional_search_paths=[TEST_RUNBOOKS_PATH]),
        additional_search_paths=[TEST_RUNBOOKS_PATH],
    )
    result = runbook_fetch_tool._invoke(
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
        runbook_fetch_tool.get_parameterized_one_liner(
            {
                "runbook_id": "test_runbook.md",
                "type": "md_file",
            }
        )
        == "Runbook: Fetch Runbook test_runbook.md"
    )
