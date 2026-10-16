import json
from unittest.mock import patch

from typer.testing import CliRunner

from holmes.main import app
from holmes.utils.doctor import CheckStatus, DoctorCheck, DoctorReport

runner = CliRunner()


def _report(*, ok: bool) -> DoctorReport:
    checks = [
        DoctorCheck(name="version", status=CheckStatus.INFO, summary="0.0.0-test"),
        DoctorCheck(
            name="llm_credentials",
            status=CheckStatus.OK if ok else CheckStatus.FAIL,
            summary="set: OPENAI_API_KEY" if ok else "No LLM credentials found",
        ),
    ]
    return DoctorReport(
        version="0.0.0-test",
        python_version="3.12.3",
        config_path="/tmp/config.yaml",
        config_exists=False,
        model="gpt-4.1",
        model_source="default",
        llm_credentials=[{"name": "OPENAI_API_KEY", "set": ok}],
        kubernetes={
            "kubectl_found": False,
            "context": None,
            "error": "kubectl not found on PATH",
        },
        tool_memory_limit_mb=800,
        toolsets=None,
        checks=checks,
    )


def test_doctor_json_success_exit_zero():
    report = _report(ok=True)
    with patch("holmes.main.collect_doctor_report", return_value=report):
        result = runner.invoke(app, ["doctor", "--json", "--skip-toolsets"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["version"] == "0.0.0-test"
    assert payload["llm_credentials"][0]["set"] is True


def test_doctor_json_failure_exit_one():
    report = _report(ok=False)
    with patch("holmes.main.collect_doctor_report", return_value=report):
        result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False


def test_doctor_table_mentions_checks():
    report = _report(ok=True)
    with patch("holmes.main.collect_doctor_report", return_value=report):
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "HolmesGPT doctor" in result.output
    assert "llm_credentials" in result.output
    assert "no failed checks" in result.output.lower()


def test_doctor_help_lists_json_flag():
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0, result.output
    assert "--json" in result.output
    assert "--skip-toolsets" in result.output
    assert "--refresh-toolsets" in result.output
