from unittest.mock import MagicMock, patch

import requests

from holmes.plugins.toolsets.prometheus.prometheus import (
    PrometheusConfig,
    _do_kube_proxy_request,
    do_request,
)


class TestDoRequestKubeProxy:
    """Test that do_request routes through kube proxy when _uses_kube_proxy is set."""

    @patch("holmes.plugins.toolsets.prometheus.prometheus._do_kube_proxy_request")
    def test_routes_through_kube_proxy_when_flag_set(self, mock_kube_request):
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_kube_request.return_value = mock_response

        config = PrometheusConfig(
            prometheus_url="https://k8s-api:6443/api/v1/namespaces/monitoring/services/prom:9090/proxy/"
        )
        config._uses_kube_proxy = True

        response = do_request(
            config=config,
            url="https://k8s-api:6443/api/v1/namespaces/monitoring/services/prom:9090/proxy/api/v1/query",
        )
        assert response.status_code == 200
        mock_kube_request.assert_called_once()

    @patch("holmes.plugins.toolsets.prometheus.prometheus.requests.request")
    def test_plain_http_when_flag_not_set(self, mock_request):
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        config = PrometheusConfig(prometheus_url="http://prometheus:9090/")
        # _uses_kube_proxy is False by default

        response = do_request(config=config, url="http://prometheus:9090/api/v1/query")
        assert response.status_code == 200
        mock_request.assert_called_once()


class TestKubeProxySession:
    @patch("holmes.plugins.toolsets.prometheus.prometheus._kube_proxy_session", None)
    @patch("holmes.plugins.toolsets.prometheus.prometheus._get_kube_proxy_session")
    def test_creates_session_on_first_use(self, mock_get_session):
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_session.verify = True
        mock_response = MagicMock(spec=requests.Response)
        mock_session.request.return_value = mock_response
        mock_get_session.return_value = mock_session

        result = _do_kube_proxy_request(method="GET", url="https://k8s:6443/proxy/test")
        mock_get_session.assert_called_once()
        assert result == mock_response


class TestPrometheusConfigKubernetesService:
    def test_kubernetes_service_field_default_none(self):
        config = PrometheusConfig(prometheus_url="http://localhost:9090")
        assert config.kubernetes_service is None

    def test_kubernetes_service_field_set(self):
        config = PrometheusConfig(kubernetes_service="monitoring/prometheus-server")
        assert config.kubernetes_service == "monitoring/prometheus-server"
        assert config.prometheus_url is None  # not resolved yet

    def test_uses_kube_proxy_default_false(self):
        config = PrometheusConfig(prometheus_url="http://localhost:9090")
        assert config._uses_kube_proxy is False
