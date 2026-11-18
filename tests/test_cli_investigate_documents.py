"""`holmes investigate opsgenie --documents` must reach the LLM prompt."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from holmes.core.issue import Issue
from holmes.main import app

runner = CliRunner()


def _invoke_opsgenie(documents: str):
    config = MagicMock()
    config.create_opsgenie_source.return_value.fetch_issues.return_value = [
        Issue(id="1", name="alert", source_type="opsgenie", source_instance_id="x")
    ]
    ai = config.create_toolcalling_llm.return_value
    ai.call.return_value.result = "analysis"
    with patch("holmes.main.Config.load_from_file", return_value=config), patch(
        "holmes.main.build_system_prompt", return_value="system"
    ), patch("holmes.main.enable_disk_token_store"):
        result = runner.invoke(
            app, ["investigate", "opsgenie", "--documents", documents]
        )
    return result, config, ai


def test_documents_are_added_to_the_user_prompt():
    result, _, ai = _invoke_opsgenie('[{"url": "https://example.com/runbook"}]')
    assert result.exit_code == 0, result.output
    messages = ai.call.call_args.args[0]
    assert "https://example.com/runbook" in messages[1]["content"]


def test_invalid_documents_fail_before_fetching_issues():
    result, config, _ = _invoke_opsgenie("not json")
    assert result.exit_code != 0
    config.create_opsgenie_source.return_value.fetch_issues.assert_not_called()
