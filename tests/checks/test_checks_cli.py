import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml
from typer.testing import CliRunner

from holmes.main import app
from holmes.core.tool_calling_llm import LLMResult


runner = CliRunner()


@patch("holmes.config.Config.create_toolcalling_llm")
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
        # Two-phase execution (#2031): investigation call + classification call.
        assert mock_ai.call.call_count == 2


@patch("holmes.config.Config.create_toolcalling_llm")
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
    # Two-phase execution (#2031): investigation call + classification call.
    assert mock_ai.call.call_count == 2


@patch("holmes.config.Config.create_toolcalling_llm")
def test_checks_cli_two_phase_no_response_format_on_investigation_call(
    mock_create_toolcalling_llm,
):
    """Phase 1 (investigation) must NOT set response_format - see #2031.

    Setting response_format alongside tool availability causes some models
    (Qwen via vLLM) to satisfy the schema immediately instead of calling
    tools first, so the investigation call must omit it entirely.
    """
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": True, "rationale": "All good."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    result = runner.invoke(
        app,
        ["checks", "run", "-c", "Are all pods healthy?", "--mode", "monitor"],
    )

    assert result.exit_code == 0, f"CLI failed with output: {result.output}"
    assert mock_ai.call.call_count == 2

    first_call_kwargs = mock_ai.call.call_args_list[0].kwargs
    assert first_call_kwargs.get("response_format") is None

    second_call_kwargs = mock_ai.call.call_args_list[1].kwargs
    assert second_call_kwargs.get("response_format") is not None


@patch("holmes.config.Config.create_toolcalling_llm")
def test_checks_cli_classification_includes_investigation_result(
    mock_create_toolcalling_llm,
):
    """Phase 2's prompt must include phase 1's investigation findings."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"

    investigation_text = "Found 3 pods in CrashLoopBackOff in namespace prod."
    investigation_response = LLMResult(result=investigation_text, tool_calls=[])
    classification_response = LLMResult(
        result=json.dumps(
            {"passed": False, "rationale": "3 pods are crash-looping."}
        ),
        tool_calls=[],
    )
    mock_ai.call.side_effect = [investigation_response, classification_response]
    mock_create_toolcalling_llm.return_value = mock_ai

    result = runner.invoke(
        app,
        ["checks", "run", "-c", "Are all pods healthy?", "--mode", "monitor"],
    )

    # Exit code 1 because the check failed, matching the existing
    # failing-check assertion pattern in this file.
    assert result.exit_code == 1, f"CLI failed unexpectedly with output: {result.output}"
    assert "FAIL" in result.output

    second_call_messages = mock_ai.call.call_args_list[1].args[0]
    combined_text = " ".join(m["content"] for m in second_call_messages)
    assert investigation_text in combined_text
