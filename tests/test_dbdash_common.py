import pytest
import requests
from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig


class TestDBADashConfig:
    def test_valid_config(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
        )
        assert config.api_url == "https://db-monitor.example.com"
        assert config.username == "holmes"
        assert config.password == "secret123"
        assert config.instance_tags is None
        assert config.verify_ssl is True
        assert config.timeout_seconds == 30

    def test_config_with_instance_tags(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
            instance_tags={"project": "payments", "environment": "production"},
        )
        assert config.instance_tags == {"project": "payments", "environment": "production"}

    def test_config_missing_required_fields(self):
        with pytest.raises(Exception):
            DBADashConfig(api_url="https://db-monitor.example.com")

    def test_config_strips_trailing_slash(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com/",
            username="holmes",
            password="secret123",
        )
        assert config.api_url == "https://db-monitor.example.com"


class TestDBADashClient:
    def _make_config(self) -> DBADashConfig:
        return DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
        )

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_login_sends_credentials(self, mock_session_cls):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user": {"username": "holmes"}}
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        client._login()

        mock_session.post.assert_called_once_with(
            "https://db-monitor.example.com/api/auth/login",
            json={"username": "holmes", "password": "secret123"},
            timeout=30,
            verify=True,
        )

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_get_request_triggers_login_on_first_call(self, mock_session_cls):
        mock_session = MagicMock()
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"user": {"username": "holmes"}}
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {"data": []}
        get_response.raise_for_status = MagicMock()
        mock_session.post.return_value = login_response
        mock_session.get.return_value = get_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        result = client.get("/api/instances")

        assert result == {"data": []}
        mock_session.post.assert_called_once()
        mock_session.get.assert_called_once()

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_get_retries_on_401(self, mock_session_cls):
        mock_session = MagicMock()
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"user": {"username": "holmes"}}
        response_401 = MagicMock()
        response_401.status_code = 401
        response_401.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=response_401
        )
        response_200 = MagicMock()
        response_200.status_code = 200
        response_200.json.return_value = {"data": "ok"}
        response_200.raise_for_status = MagicMock()

        mock_session.post.return_value = login_response
        mock_session.get.side_effect = [response_401, response_200]
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        result = client.get("/api/health")

        assert result == {"data": "ok"}
        assert mock_session.post.call_count == 2
        assert mock_session.get.call_count == 2

    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_login_failure_raises(self, mock_session_cls):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid credentials"
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        with pytest.raises(requests.exceptions.HTTPError):
            client._login()
