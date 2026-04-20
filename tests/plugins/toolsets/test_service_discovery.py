from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.service_discovery import (
    DiscoveredService,
    build_service_url,
    find_service,
    find_service_url,
    resolve_kubernetes_service,
)


class TestDiscoveredService:
    def test_dataclass_fields(self):
        svc = DiscoveredService(name="prometheus", namespace="monitoring", port=9090)
        assert svc.name == "prometheus"
        assert svc.namespace == "monitoring"
        assert svc.port == 9090


class TestBuildServiceUrl:
    @patch(
        "holmes.plugins.toolsets.service_discovery.is_running_in_cluster",
        return_value=True,
    )
    def test_in_cluster_url(self, _mock_in_cluster):
        svc = DiscoveredService(
            name="prometheus-server", namespace="monitoring", port=9090
        )
        url = build_service_url(svc)
        assert url == "http://prometheus-server.monitoring.svc.cluster.local:9090"

    @patch(
        "holmes.plugins.toolsets.service_discovery.is_running_in_cluster",
        return_value=False,
    )
    @patch("holmes.plugins.toolsets.service_discovery.client.ApiClient")
    def test_local_kube_proxy_url(self, mock_api_client_cls, _mock_in_cluster):
        mock_cfg = MagicMock()
        mock_cfg.host = "https://127.0.0.1:6443"
        mock_api_client = MagicMock()
        mock_api_client.configuration = mock_cfg
        mock_api_client_cls.return_value = mock_api_client

        svc = DiscoveredService(
            name="prometheus-server", namespace="monitoring", port=9090
        )
        url = build_service_url(svc)
        assert (
            url
            == "https://127.0.0.1:6443/api/v1/namespaces/monitoring/services/prometheus-server:9090/proxy/"
        )

    @patch(
        "holmes.plugins.toolsets.service_discovery.is_running_in_cluster",
        return_value=False,
    )
    @patch("holmes.plugins.toolsets.service_discovery.client.ApiClient")
    def test_local_url_strips_trailing_slash(
        self, mock_api_client_cls, _mock_in_cluster
    ):
        mock_cfg = MagicMock()
        mock_cfg.host = "https://k8s.example.com:6443/"
        mock_api_client = MagicMock()
        mock_api_client.configuration = mock_cfg
        mock_api_client_cls.return_value = mock_api_client

        svc = DiscoveredService(name="prom", namespace="ns", port=8080)
        url = build_service_url(svc)
        assert (
            url
            == "https://k8s.example.com:6443/api/v1/namespaces/ns/services/prom:8080/proxy/"
        )


class TestFindService:
    @patch("holmes.plugins.toolsets.service_discovery.client.CoreV1Api")
    def test_returns_discovered_service(self, mock_core_api_cls):
        mock_svc = MagicMock()
        mock_svc.metadata.name = "prometheus-server"
        mock_svc.metadata.namespace = "monitoring"
        mock_svc.spec.ports = [MagicMock(port=9090)]

        mock_api = MagicMock()
        mock_api.list_service_for_all_namespaces.return_value = MagicMock(
            items=[mock_svc]
        )
        mock_core_api_cls.return_value = mock_api

        result = find_service("app=prometheus-server")
        assert result is not None
        assert result.name == "prometheus-server"
        assert result.namespace == "monitoring"
        assert result.port == 9090

    @patch("holmes.plugins.toolsets.service_discovery.client.CoreV1Api")
    def test_returns_none_when_no_services(self, mock_core_api_cls):
        mock_api = MagicMock()
        mock_api.list_service_for_all_namespaces.return_value = MagicMock(items=[])
        mock_core_api_cls.return_value = mock_api

        result = find_service("app=nonexistent")
        assert result is None

    @patch("holmes.plugins.toolsets.service_discovery.client.CoreV1Api")
    def test_returns_none_on_exception(self, mock_core_api_cls):
        mock_core_api_cls.side_effect = Exception("API error")
        result = find_service("app=broken")
        assert result is None


class TestFindServiceUrl:
    @patch(
        "holmes.plugins.toolsets.service_discovery.build_service_url",
        return_value="http://prom.monitoring.svc.cluster.local:9090",
    )
    @patch("holmes.plugins.toolsets.service_discovery.find_service")
    def test_returns_url(self, mock_find, mock_build):
        mock_find.return_value = DiscoveredService(
            name="prom", namespace="monitoring", port=9090
        )
        url = find_service_url("app=prometheus")
        assert url == "http://prom.monitoring.svc.cluster.local:9090"

    @patch("holmes.plugins.toolsets.service_discovery.find_service", return_value=None)
    def test_returns_none_when_not_found(self, _mock_find):
        url = find_service_url("app=nonexistent")
        assert url is None


class TestResolveKubernetesService:
    @patch("holmes.plugins.toolsets.service_discovery.client.CoreV1Api")
    def test_resolves_valid_service(self, mock_core_api_cls):
        mock_svc = MagicMock()
        mock_svc.spec.ports = [MagicMock(port=9090)]

        mock_api = MagicMock()
        mock_api.read_namespaced_service.return_value = mock_svc
        mock_core_api_cls.return_value = mock_api

        result = resolve_kubernetes_service("monitoring/prometheus-server")
        assert result is not None
        assert result.name == "prometheus-server"
        assert result.namespace == "monitoring"
        assert result.port == 9090
        mock_api.read_namespaced_service.assert_called_once_with(
            name="prometheus-server", namespace="monitoring"
        )

    def test_rejects_invalid_format_no_slash(self):
        result = resolve_kubernetes_service("prometheus-server")
        assert result is None

    def test_rejects_invalid_format_empty_parts(self):
        result = resolve_kubernetes_service("/prometheus-server")
        assert result is None
        result = resolve_kubernetes_service("monitoring/")
        assert result is None

    @patch("holmes.plugins.toolsets.service_discovery.client.CoreV1Api")
    def test_returns_none_on_api_error(self, mock_core_api_cls):
        mock_api = MagicMock()
        mock_api.read_namespaced_service.side_effect = Exception("not found")
        mock_core_api_cls.return_value = mock_api

        result = resolve_kubernetes_service("monitoring/nonexistent")
        assert result is None
