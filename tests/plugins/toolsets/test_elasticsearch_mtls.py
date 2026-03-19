"""Tests for Elasticsearch mTLS (mutual TLS) configuration."""

from unittest.mock import MagicMock, patch

import pytest

from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
    ElasticsearchClusterToolset,
    ElasticsearchConfig,
)


class TestElasticsearchMTLSConfig:
    """Tests for mTLS configuration validation."""

    def test_mtls_config_valid(self):
        """Test that valid mTLS config is accepted."""
        config = ElasticsearchConfig(
            api_url="https://es:9200",
            client_cert="/path/to/client.crt",
            client_key="/path/to/client.key",
        )
        assert config.client_cert == "/path/to/client.crt"
        assert config.client_key == "/path/to/client.key"

    def test_mtls_config_cert_without_key_fails(self):
        """Test that client_cert without client_key raises error."""
        with pytest.raises(ValueError, match="client_key is required"):
            ElasticsearchConfig(
                api_url="https://es:9200",
                client_cert="/path/to/client.crt",
            )

    def test_mtls_config_key_without_cert_fails(self):
        """Test that client_key without client_cert raises error."""
        with pytest.raises(ValueError, match="client_cert is required"):
            ElasticsearchConfig(
                api_url="https://es:9200",
                client_key="/path/to/client.key",
            )

    def test_ca_cert_accepted_but_ignored(self):
        """Test that ca_cert is accepted for backwards compat but ignored."""
        config = ElasticsearchConfig(
            api_url="https://es:9200",
            ca_cert="/path/to/ca.crt",
        )
        assert not hasattr(config, "ca_cert") or config.model_fields.get("ca_cert") is None
        assert config.client_cert is None

    def test_config_without_mtls(self):
        """Test that config works without mTLS fields."""
        config = ElasticsearchConfig(
            api_url="https://es:9200",
            api_key="test-key",
        )
        assert config.client_cert is None
        assert config.client_key is None


class TestElasticsearchMTLSRequest:
    """Tests for mTLS request handling."""

    def test_get_client_cert_returns_tuple(self):
        """Test that _get_client_cert returns cert/key tuple when configured."""
        toolset = ElasticsearchClusterToolset()
        toolset.config = ElasticsearchConfig(
            api_url="https://es:9200",
            client_cert="/path/to/client.crt",
            client_key="/path/to/client.key",
        )
        result = toolset._get_client_cert()
        assert result == ("/path/to/client.crt", "/path/to/client.key")

    def test_get_client_cert_returns_none(self):
        """Test that _get_client_cert returns None when not configured."""
        toolset = ElasticsearchClusterToolset()
        toolset.config = ElasticsearchConfig(
            api_url="https://es:9200",
            api_key="test-key",
        )
        assert toolset._get_client_cert() is None

    def test_get_verify_returns_bool(self):
        """Test that _get_verify returns verify_ssl boolean."""
        toolset = ElasticsearchClusterToolset()
        toolset.config = ElasticsearchConfig(
            api_url="https://es:9200",
            verify_ssl=False,
        )
        assert toolset._get_verify() is False

    def test_get_verify_defaults_true(self):
        """Test that _get_verify defaults to True."""
        toolset = ElasticsearchClusterToolset()
        toolset.config = ElasticsearchConfig(
            api_url="https://es:9200",
        )
        assert toolset._get_verify() is True

    @patch("holmes.plugins.toolsets.elasticsearch.elasticsearch.requests.request")
    def test_make_request_passes_mtls_params(self, mock_request):
        """Test that _make_request passes cert and verify to requests."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "green"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        toolset = ElasticsearchClusterToolset()
        toolset.config = ElasticsearchConfig(
            api_url="https://es:9200",
            client_cert="/path/to/client.crt",
            client_key="/path/to/client.key",
        )

        toolset._make_request("GET", "_cluster/health")

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["cert"] == ("/path/to/client.crt", "/path/to/client.key")
        assert call_kwargs["verify"] is True

    @patch("holmes.plugins.toolsets.elasticsearch.elasticsearch.requests.request")
    def test_make_request_without_mtls(self, mock_request):
        """Test that _make_request works without mTLS (cert=None, verify=True)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "green"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        toolset = ElasticsearchClusterToolset()
        toolset.config = ElasticsearchConfig(
            api_url="https://es:9200",
            api_key="test-key",
        )

        toolset._make_request("GET", "_cluster/health")

        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["cert"] is None
        assert call_kwargs["verify"] is True
