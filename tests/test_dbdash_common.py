import pytest
import requests
from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig


class TestDBADashConfig:
    def test_valid_config_with_direct_credentials(self):
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
        assert config.secrets_manager_arn is None

    def test_config_with_instance_tags(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
            instance_tags={"project": "payments", "environment": "production"},
        )
        assert config.instance_tags == {"project": "payments", "environment": "production"}

    def test_config_missing_credentials_and_no_arn_raises(self):
        with pytest.raises(Exception):
            DBADashConfig(api_url="https://db-monitor.example.com")

    def test_config_strips_trailing_slash(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com/",
            username="holmes",
            password="secret123",
        )
        assert config.api_url == "https://db-monitor.example.com"

    @patch("holmes.plugins.toolsets.dbdash.common._fetch_secret")
    def test_config_with_secrets_manager_arn(self, mock_fetch):
        mock_fetch.return_value = {
            "username": "svc-account@example.com",
            "password": "secret-from-sm",
            "cognito_user_pool_id": "us-east-1_FROMARN",
            "cognito_client_id": "client-from-arn",
        }
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            secrets_manager_arn="arn:aws:secretsmanager:us-east-1:123456:secret:test-secret",
        )
        assert config.username == "svc-account@example.com"
        assert config.password == "secret-from-sm"
        assert config.cognito_user_pool_id == "us-east-1_FROMARN"
        assert config.cognito_client_id == "client-from-arn"


class TestDBADashClient:
    def _make_config(self, **kwargs) -> DBADashConfig:
        defaults = dict(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
            cognito_user_pool_id="us-east-1_TESTPOOL",
            cognito_client_id="test-client-id",
        )
        defaults.update(kwargs)
        return DBADashConfig(**defaults)

    @patch("holmes.plugins.toolsets.dbdash.common.Cognito")
    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_login_authenticates_via_cognito_then_exchanges_token(self, mock_session_cls, mock_cognito_cls):
        # Mock Cognito SRP
        mock_cognito = MagicMock()
        mock_cognito.id_token = "fake-id-token"
        mock_cognito.refresh_token = "fake-refresh-token"
        mock_cognito_cls.return_value = mock_cognito

        # Mock dbdash-web login
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "user": {"username": "holmes"}}
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        client = DBADashClient(self._make_config())
        client._login()

        # Verify Cognito was called with correct params
        mock_cognito_cls.assert_called_once_with("us-east-1_TESTPOOL", "test-client-id", username="holmes")
        mock_cognito.authenticate.assert_called_once_with(password="secret123")

        # Verify dbdash-web login was called with the Cognito tokens
        mock_session.post.assert_called_once_with(
            "https://db-monitor.example.com/api/auth/login",
            json={"idToken": "fake-id-token", "refreshToken": "fake-refresh-token"},
            timeout=30,
        )

    @patch("holmes.plugins.toolsets.dbdash.common.Cognito")
    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_get_request_triggers_login_on_first_call(self, mock_session_cls, mock_cognito_cls):
        mock_cognito = MagicMock()
        mock_cognito.id_token = "fake-id-token"
        mock_cognito.refresh_token = "fake-refresh-token"
        mock_cognito_cls.return_value = mock_cognito

        mock_session = MagicMock()
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"success": True}
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
        mock_session.post.assert_called_once()  # login
        mock_session.get.assert_called_once()   # GET /api/instances

    @patch("holmes.plugins.toolsets.dbdash.common.Cognito")
    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_get_retries_on_401(self, mock_session_cls, mock_cognito_cls):
        mock_cognito = MagicMock()
        mock_cognito.id_token = "fake-id-token"
        mock_cognito.refresh_token = "fake-refresh-token"
        mock_cognito_cls.return_value = mock_cognito

        mock_session = MagicMock()
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"success": True}
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
        assert mock_session.post.call_count == 2  # login + re-login
        assert mock_session.get.call_count == 2   # 401 + retry

    @patch("holmes.plugins.toolsets.dbdash.common.Cognito")
    def test_cognito_auth_failure_raises(self, mock_cognito_cls):
        mock_cognito = MagicMock()
        mock_cognito.authenticate.side_effect = Exception("Incorrect username or password")
        mock_cognito_cls.return_value = mock_cognito

        client = DBADashClient(self._make_config())
        with pytest.raises(Exception, match="Incorrect username or password"):
            client._login()

    def test_fetches_cognito_config_when_not_provided(self):
        config = DBADashConfig(
            api_url="https://db-monitor.example.com",
            username="holmes",
            password="secret123",
            # cognito_user_pool_id and cognito_client_id NOT set
        )
        client = DBADashClient(config)

        with patch("holmes.plugins.toolsets.dbdash.common.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"userPoolId": "us-east-1_AUTO", "clientId": "auto-client"}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            client._fetch_cognito_config()

            assert client._cognito_user_pool_id == "us-east-1_AUTO"
            assert client._cognito_client_id == "auto-client"
            mock_get.assert_called_once_with(
                "https://db-monitor.example.com/api/auth/config",
                timeout=30,
                verify=True,
            )
