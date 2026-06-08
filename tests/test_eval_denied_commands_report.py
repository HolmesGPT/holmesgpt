"""Tests for the "Denied commands" column in the eval report.

The eval report (evals_report.md, posted on CI/CD and GitHub Actions) includes a
column listing the bash commands HolmesGPT tried to run that were denied. During
evals there is no interactive approver and the bash toolset enforces an
allow/deny list, so any command that is not pre-approved is effectively denied.
"""

from holmes.core.models import ToolCallResult
from holmes.core.tool_calling_llm import LLMResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from tests.llm.utils.denied_commands import extract_denied_commands
from tests.llm.utils.reporting.github_reporter import (
    _fmt_denied_commands,
    generate_markdown_report,
)


def _bash_tc(call_id, command, status, error=None):
    return ToolCallResult(
        tool_call_id=call_id,
        tool_name="bash",
        description=command,
        result=StructuredToolResult(
            status=status, error=error, invocation=command
        ),
    )


def test_extract_denied_commands_picks_up_denials_and_approval_required():
    tool_calls = [
        # Deny-list / hard-coded block.
        _bash_tc(
            "1",
            "kubectl get secret mysecret",
            StructuredToolResultStatus.ERROR,
            "Command blocked by configuration: matches deny list entry",
        ),
        # Approval required but rejected in non-interactive mode.
        _bash_tc(
            "2",
            "rm -rf /tmp/foo",
            StructuredToolResultStatus.ERROR,
            "Tool call rejected for security reasons: Command requires approval.",
        ),
        # Raw APPROVAL_REQUIRED status.
        _bash_tc(
            "3",
            "psql -c select",
            StructuredToolResultStatus.APPROVAL_REQUIRED,
            "Command requires approval.",
        ),
    ]

    assert extract_denied_commands(tool_calls) == [
        "kubectl get secret mysecret",
        "rm -rf /tmp/foo",
        "psql -c select",
    ]


def test_extract_denied_commands_ignores_non_denials():
    tool_calls = [
        # Successful bash command.
        _bash_tc("1", "kubectl get pods", StructuredToolResultStatus.SUCCESS),
        # Bash command that ran but exited non-zero (not a denial).
        _bash_tc(
            "2",
            "kubectl get xyz",
            StructuredToolResultStatus.ERROR,
            'Error: Command "kubectl get xyz" returned non-zero exit status 1',
        ),
        # Non-bash tool with a blocked-looking error must not be attributed to bash.
        ToolCallResult(
            tool_call_id="3",
            tool_name="kubectl_describe",
            description="describe",
            result=StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error="Command blocked"
            ),
        ),
    ]

    assert extract_denied_commands(tool_calls) == []


def test_extract_denied_commands_handles_empty_input():
    assert extract_denied_commands(None) == []
    assert extract_denied_commands([]) == []


def test_extract_denied_commands_works_through_llmresult_coercion():
    """LLMResult.tool_calls is populated from to_client_dict() dicts and coerced
    back into ToolCallResult by pydantic — the extractor must work on that path."""
    denied = _bash_tc(
        "1",
        "kubectl get secret mysecret",
        StructuredToolResultStatus.ERROR,
        "Command blocked by configuration: deny list",
    )
    allowed = _bash_tc("2", "kubectl get pods", StructuredToolResultStatus.SUCCESS)

    result = LLMResult(
        result="done",
        tool_calls=[denied.to_client_dict(), allowed.to_client_dict()],
    )

    assert all(isinstance(tc, ToolCallResult) for tc in result.tool_calls)
    assert extract_denied_commands(result.tool_calls) == ["kubectl get secret mysecret"]


def test_fmt_denied_commands_escapes_table_breaking_characters():
    assert _fmt_denied_commands([]) == "—"
    assert _fmt_denied_commands(None) == "—"

    rendered = _fmt_denied_commands(
        ["kubectl get secret foo", "kubectl get pods | grep x"]
    )
    # Each command wrapped in backticks, pipes escaped, stacked with <br>.
    assert rendered == "`kubectl get secret foo`<br>`kubectl get pods \\| grep x`"


def test_report_includes_denied_commands_column():
    results = [
        {
            "test_type": "ask",
            "test_case_name": "197_bash_secrets_denied",
            "status": "passed",
            "outcome": "passed",
            "actual_correctness_score": 1.0,
            "expected_correctness_score": 1.0,
            "holmes_duration": 3.2,
            "num_llm_calls": 2,
            "tool_call_count": 3,
            "cost": 0.01,
            "total_tokens": 1000,
            "prompt_tokens": 800,
            "completion_tokens": 200,
            "denied_commands": [
                "kubectl get secret mysecret",
                "kubectl describe secret foo",
            ],
        },
        {
            "test_type": "ask",
            "test_case_name": "01_how_many_pods",
            "status": "passed",
            "outcome": "passed",
            "actual_correctness_score": 1.0,
            "expected_correctness_score": 1.0,
            "holmes_duration": 1.0,
            "num_llm_calls": 1,
            "tool_call_count": 1,
            "cost": 0.005,
            "total_tokens": 500,
            "prompt_tokens": 400,
            "completion_tokens": 100,
            "denied_commands": [],
        },
    ]

    markdown, _, _ = generate_markdown_report(results, include_historical=False)

    # Column header is present.
    assert "Denied commands" in markdown
    # The denied commands of the first test appear, the second shows an em dash.
    assert "`kubectl get secret mysecret`<br>`kubectl describe secret foo`" in markdown
    # The Total row aggregates the count of denied commands across tests.
    header, separator, *body = [
        line for line in markdown.splitlines() if line.startswith("|")
    ]
    # Header and separator have the same number of columns as the body rows.
    assert header.count("|") == separator.count("|")
    for row in body:
        assert row.count("|") == header.count("|")
    total_row = next(row for row in body if "**Total**" in row)
    assert "**2**" in total_row

    # A warning line above the table announces the total denied bash command count.
    assert "**Warning:** this eval run contains 2 denied bash commands." in markdown
    warning_idx = markdown.index("denied bash commands.")
    table_idx = markdown.index("| Status | Test case |")
    assert warning_idx < table_idx, "warning must appear above the table"


def test_report_warning_omitted_when_no_denied_commands():
    results = [
        {
            "test_type": "ask",
            "test_case_name": "01_how_many_pods",
            "status": "passed",
            "outcome": "passed",
            "actual_correctness_score": 1.0,
            "expected_correctness_score": 1.0,
            "holmes_duration": 1.0,
            "num_llm_calls": 1,
            "tool_call_count": 1,
            "denied_commands": [],
        },
    ]

    markdown, _, _ = generate_markdown_report(results, include_historical=False)

    assert "denied bash command" not in markdown


def test_report_warning_singular_phrasing():
    results = [
        {
            "test_type": "ask",
            "test_case_name": "260_bash_denied_command",
            "status": "passed",
            "outcome": "passed",
            "actual_correctness_score": 1.0,
            "expected_correctness_score": 1.0,
            "holmes_duration": 1.0,
            "num_llm_calls": 1,
            "tool_call_count": 1,
            "denied_commands": ["ps aux"],
        },
    ]

    markdown, _, _ = generate_markdown_report(results, include_historical=False)

    assert "this eval run contains 1 denied bash command." in markdown
    assert "1 denied bash commands." not in markdown
