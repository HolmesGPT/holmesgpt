from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.runbook.runbook_fetcher import (
    RunbookFetcher,
    RunbookToolset,
)
from tests.conftest import create_mock_tool_invoke_context


def test_RunbookFetcher():
    runbook_fetch_tool = RunbookFetcher(RunbookToolset(dal=None))
    result = runbook_fetch_tool._invoke(
        {"runbook_id": "wrong_runbook_path.md", "type": "md_file"},
        context=create_mock_tool_invoke_context(),
    )
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.error is not None

    result = runbook_fetch_tool._invoke(
        {
            "runbook_id": "networking/dns_troubleshooting_instructions.md",
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
                "runbook_id": "networking/dns_troubleshooting_instructions.md",
                "type": "md_file",
            }
        )
        == "Runbook: Fetch Runbook networking/dns_troubleshooting_instructions.md"
    )
