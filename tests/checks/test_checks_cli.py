import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml
from typer.testing import CliRunner

from holmes.main import app
from holmes.core.tool_calling_llm import LLMResult


runner = CliRunner()


@patch("holmes.config.Config.create_console_toolcalling_llm")
def test_checks_cli_monitor_mode(mock_create_toolcalling_llm):
    """Test running a check in monitor mode via CLI with mocked LLM."""
    # Create mock AI
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"

    # Mock LLM response for a passing check
    mock_response = LLMResult(
        result=json.dumps(
            {
                "passed": True,
                "rationale": "All pods are running correctly in the namespace.",
            }
        ),
        tool_calls=[],
    )
    mock_ai.call.return_value = mock_response
    mock_create_toolcalling_llm.return_value = mock_ai

    # Create a temporary checks config file
    checks_config = {
        "version": 1,
        "checks": [
            {
                "name": "test-pod-check",
                "query": "Are all pods running in the default namespace?",
                "description": "Verify pods are healthy",
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as f:
        yaml.dump(checks_config, f)
        checks_file = Path(f.name)

        # Run CLI in monitor mode
        # Note: checks_app already has "check" as the command name, so we just pass the options
        result = runner.invoke(
            app,
            ["checks", "run", "--checks-file", str(checks_file), "--mode", "monitor"],
        )

        # Verify CLI executed successfully
        assert result.exit_code == 0, f"CLI failed with output: {result.output}"

        # Verify check results appear in output
        assert "test-pod-check" in result.output
        assert "PASS" in result.output

        # Verify LLM was called
        mock_ai.call.assert_called_once()


@patch("holmes.config.Config.create_console_toolcalling_llm")
def test_checks_cli_inline_check(mock_create_toolcalling_llm):
    """Test running an inline check via CLI with -c option."""
    # Create mock AI
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"

    # Mock LLM response for a failing check
    mock_response = LLMResult(
        result=json.dumps(
            {
                "passed": False,
                "rationale": "Found 2 pods in CrashLoopBackOff state.",
            }
        ),
        tool_calls=[],
    )
    mock_ai.call.return_value = mock_response
    mock_create_toolcalling_llm.return_value = mock_ai

    # Run CLI with inline check
    result = runner.invoke(
        app,
        [
            "checks",
            "run",
            "-c",
            "Are all pods healthy in the cluster?",
            "--mode",
            "monitor",
        ],
    )

    # Verify CLI executed (exit code 1 because the check failed)
    assert (
        result.exit_code == 1
    ), f"CLI failed unexpectedly with output: {result.output}"

    # Verify check results appear in output
    assert "Inline Check" in result.output
    assert "FAIL" in result.output

    # Verify LLM was called
    mock_ai.call.assert_called_once()
