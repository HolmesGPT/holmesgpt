from unittest.mock import MagicMock, patch

import pytest

from holmes.plugins.sources.pagerduty import PagerDutySource

SAMPLE_INCIDENT = {
    "id": "Q1EOHXXWSUNEKN",
    "summary": "[FIRING:1] TestAlert Grafana",
    "html_url": "https://example.pagerduty.com/incidents/Q1EOHXXWSUNEKN",
    "description": "Alert triggered from Grafana",
}

SAMPLE_ALERTS_RESPONSE = {
    "alerts": [
        {
            "body": {
                "details": {
                    "firing": (
                        "Value: [no value]\n"
                        "Labels: alertname = TestAlert, instance = Grafana\n"
                        "Annotations:\n"
                        " - description = fluentd pods are not healthy in us-west-2-non-prod\n"
                        " - summary = fluentd pods are not healthy"
                    ),
                    "num_firing": "1",
                }
            }
        }
    ]
}


class TestConvertToIssue:
    def test_sets_description_from_incident(self):
        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.convert_to_issue(SAMPLE_INCIDENT)
        assert issue.description == "Alert triggered from Grafana"

    def test_description_none_when_missing(self):
        incident = {**SAMPLE_INCIDENT}
        del incident["description"]
        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.convert_to_issue(incident)
        assert issue.description is None

    def test_basic_fields_are_set(self):
        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.convert_to_issue(SAMPLE_INCIDENT)
        assert issue.id == "Q1EOHXXWSUNEKN"
        assert issue.name == "[FIRING:1] TestAlert Grafana"
        assert issue.source_type == "pagerduty"
        assert "Q1EOHXXWSUNEKN" in issue.url


class TestFetchIssueAlertEnrichment:
    @patch("holmes.plugins.sources.pagerduty.requests.get")
    def test_enriches_description_with_alert_details(self, mock_get):
        incident_response = MagicMock()
        incident_response.status_code = 200
        incident_response.json.return_value = {"incident": SAMPLE_INCIDENT}

        alerts_response = MagicMock()
        alerts_response.status_code = 200
        alerts_response.json.return_value = SAMPLE_ALERTS_RESPONSE

        mock_get.side_effect = [incident_response, alerts_response]

        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.fetch_issue("Q1EOHXXWSUNEKN")

        assert issue is not None
        assert "Alert triggered from Grafana" in issue.description
        assert "num_firing: 1" in issue.description
        assert "firing:" in issue.description

    @patch("holmes.plugins.sources.pagerduty.requests.get")
    def test_graceful_on_empty_alerts(self, mock_get):
        incident_response = MagicMock()
        incident_response.status_code = 200
        incident_response.json.return_value = {"incident": SAMPLE_INCIDENT}

        alerts_response = MagicMock()
        alerts_response.status_code = 200
        alerts_response.json.return_value = {"alerts": []}

        mock_get.side_effect = [incident_response, alerts_response]

        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.fetch_issue("Q1EOHXXWSUNEKN")

        assert issue is not None
        assert issue.description == "Alert triggered from Grafana"

    @patch("holmes.plugins.sources.pagerduty.requests.get")
    def test_graceful_on_alerts_api_failure(self, mock_get):
        incident_response = MagicMock()
        incident_response.status_code = 200
        incident_response.json.return_value = {"incident": SAMPLE_INCIDENT}

        alerts_response = MagicMock()
        alerts_response.raise_for_status.side_effect = Exception("API error")

        mock_get.side_effect = [incident_response, alerts_response]

        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.fetch_issue("Q1EOHXXWSUNEKN")

        assert issue is not None
        assert issue.description == "Alert triggered from Grafana"

    @patch("holmes.plugins.sources.pagerduty.requests.get")
    def test_alert_details_without_incident_description(self, mock_get):
        """When the incident has no description, alert details become the full description."""
        incident_no_desc = {**SAMPLE_INCIDENT, "description": None}
        incident_response = MagicMock()
        incident_response.status_code = 200
        incident_response.json.return_value = {"incident": incident_no_desc}

        alerts_response = MagicMock()
        alerts_response.status_code = 200
        alerts_response.json.return_value = SAMPLE_ALERTS_RESPONSE

        mock_get.side_effect = [incident_response, alerts_response]

        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issue = source.fetch_issue("Q1EOHXXWSUNEKN")

        assert issue is not None
        assert issue.description is not None
        assert "num_firing: 1" in issue.description
        assert "firing:" in issue.description


class TestFetchIssues:
    @patch("holmes.plugins.sources.pagerduty.requests.get")
    def test_fetch_issues_still_works(self, mock_get):
        """Backward compatibility: fetch_issues (plural) sets description from incident data."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "incidents": [SAMPLE_INCIDENT]
        }

        mock_get.return_value = response

        source = PagerDutySource(api_key="k", user_email="u@e.com")
        issues = source.fetch_issues()

        assert len(issues) == 1
        assert issues[0].description == "Alert triggered from Grafana"
