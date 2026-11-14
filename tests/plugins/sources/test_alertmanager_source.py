import json

from holmes.plugins.sources.prometheus.plugin import AlertManagerSource


def test_fetch_issues_without_severity_label(tmp_path):
    alerts_file = tmp_path / "alerts.json"
    alerts_file.write_text(
        json.dumps(
            [
                {
                    "status": "firing",
                    "labels": {"alertname": "MyAlert"},
                    "annotations": {},
                    "startsAt": "2026-11-14T00:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "fingerprint": "abc",
                }
            ]
        )
    )
    source = AlertManagerSource(url=None, filepath=str(alerts_file))  # type: ignore[arg-type]

    issues = source.fetch_issues()

    assert [issue.name for issue in issues] == ["MyAlert"]
