"""Integration tests for the /api/instances/{id}/test-connection endpoint helpers."""

import asyncio
from unittest.mock import patch, MagicMock
import pytest


class TestPagerDutyConnectionHelper:
    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    @patch("projects._fetch_secret")
    def test_connection_success(self, mock_secret, mock_get):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-project-x",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-x",
            config={"service_ids": ["PSVC1"]},
        )
        mock_secret.return_value = {"api_key": "good-key"}

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"services": []}
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.get_event_loop().run_until_complete(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    @patch("projects._fetch_secret")
    def test_connection_401_returns_clear_error(self, mock_secret, mock_get):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-bad",
        )
        mock_secret.return_value = {"api_key": "bad"}

        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.get_event_loop().run_until_complete(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is False
        assert body["status"] == "error"
        assert "invalid or expired" in body["error"]

    def test_connection_no_credential_source(self):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-empty",
        )
        store = MagicMock()
        body = asyncio.get_event_loop().run_until_complete(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is False
        assert "no credential source" in body["error"]

    @patch("projects._fetch_secret")
    def test_connection_secret_missing_api_key(self, mock_secret):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-bad-secret",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-bad",
        )
        mock_secret.return_value = {"some_other_key": "x"}  # no api_key
        store = MagicMock()
        body = asyncio.get_event_loop().run_until_complete(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is False
        assert "no 'api_key' field" in body["error"]
