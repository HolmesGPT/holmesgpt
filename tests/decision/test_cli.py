import json

from typer.testing import CliRunner

from holmes.decision.cli import decision_app


runner = CliRunner()


def test_demo_inspect_and_evidence_cli(tmp_path):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    completed = runner.invoke(decision_app, ["demo", "--database-url", url, "--json"])
    assert completed.exit_code == 0, completed.output
    result = json.loads(completed.output)
    task_id = result["state"]["task_id"]
    assert result["state"]["context"]["mode"] == "offline_simulation"
    assert result["state"]["tool_calls"] == 4
    evidence_id = result["state"]["evidence"][0]["id"]
    inspection = runner.invoke(decision_app, ["inspect", task_id, "--database-url", url])
    assert inspection.exit_code == 0, inspection.output
    assert json.loads(inspection.output)["state"]["status"] == "completed"
    raw = runner.invoke(decision_app, ["evidence", task_id, evidence_id, "--database-url", url])
    assert raw.exit_code == 0, raw.output
    assert "SQLTransient" in json.loads(raw.output)["content"]


def test_live_mode_requires_jev_credentials(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = runner.invoke(decision_app, ["investigate", "checkout timeout"])
    assert result.exit_code != 0
    assert "TYPESAFE_API_KEY" in result.output
