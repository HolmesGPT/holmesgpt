import json

from holmes.plugins.sources.prometheus.plugin import AlertManagerSource

SAMPLE_ALERT = {
    "status": "firing",
    "labels": {"alertname": "KubePodCrashLooping", "severity": "warning"},
    "annotations": {},
    "startsAt": "2026-11-14T00:00:00Z",
    "endsAt": "0001-01-01T00:00:00Z",
    "fingerprint": "aaa",
}


def test_fetch_issues_from_file(tmp_path):
    alerts_file = tmp_path / "alerts.json"
    alerts_file.write_text(json.dumps([SAMPLE_ALERT]))

    # Config passes url=None when --alertmanager-file is used
    source = AlertManagerSource(url=None, filepath=alerts_file)  # type: ignore[arg-type]
    issues = source.fetch_issues()

    assert len(issues) == 1
    assert issues[0].name == "KubePodCrashLooping"
    assert issues[0].source_instance_id == str(alerts_file)
