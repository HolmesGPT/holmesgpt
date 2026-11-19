"""Exercise Kubernetes SDK compatibility against an isolated HTTP API stub."""

import base64
import importlib
import json
import ssl
import sys
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock

import certifi
import pytest
from freezegun import freeze_time
from kubernetes import client, config
from kubernetes.aio import client as aio_client
from kubernetes.client.rest import ApiException
from urllib3.exceptions import MaxRetryError

from holmes.core import self_logs
from holmes.plugins.toolsets import service_discovery
from holmes.utils import cert_utils
from holmes.utils.kubernetes_utils import load_kube_config_with_certifi
from holmes_operator.models import CheckPhase
from holmes_operator import context as operator_context
from holmes_operator.utils import update_healthcheck_status
from tests.plugins.toolsets.test_prometheus_ssl import generate_self_signed_cert


@pytest.fixture
def api_server(request, tmp_path, monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    routes = {}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            requests.append(
                {
                    "method": self.command,
                    "url": urlsplit(self.path),
                    "headers": self.headers,
                    "body": json.loads(body) if body else None,
                }
            )
            route = routes.get(
                (self.command, urlsplit(self.path).path),
                (404, {"message": "Unexpected Kubernetes API request"}),
            )
            if callable(route):
                route(self)
                return
            status, payload = route
            is_json = not isinstance(payload, str)
            data = (json.dumps(payload) if is_json else payload).encode()
            self.send_response(status)
            self.send_header(
                "Content-Type", "application/json" if is_json else "text/plain"
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = respond
        do_PATCH = respond

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    scheme = "http"
    if getattr(request, "param", False):
        cert_file = tmp_path / "ca.crt"
        key_file = tmp_path / "server.key"
        generate_self_signed_cert(str(cert_file), str(key_file))
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(str(cert_file), str(key_file))
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"{scheme}://localhost:{server.server_port}", routes, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _kubeconfig(host, user):
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": "test",
        "contexts": [{"name": "test", "context": {"cluster": "test", "user": "test"}}],
        "clusters": [{"name": "test", "cluster": {"server": host}}],
        "users": [{"name": "test", "user": user}],
    }


def _kube_configuration(host, user):
    configuration = client.Configuration()
    config.load_kube_config_from_dict(
        _kubeconfig(host, user),
        client_configuration=configuration,
        persist_config=False,
    )
    return configuration


def _services(items):
    return {"apiVersion": "v1", "kind": "ServiceList", "items": items}


@pytest.mark.parametrize("has_service", [True, False])
def test_service_discovery_uses_generated_service_models(
    api_server, monkeypatch, caplog, has_service
):
    host, routes, requests = api_server
    service = {
        "metadata": {"name": "prometheus", "namespace": "monitoring"},
        "spec": {"ports": [{"port": 9090}]},
    }
    routes["GET", "/api/v1/services"] = (
        200,
        _services([service] if has_service else []),
    )
    configuration = _kube_configuration(host, {"token": "test-token"})
    with client.ApiClient(configuration) as api_client:
        api = client.CoreV1Api(api_client)
        monkeypatch.setattr(service_discovery.client, "CoreV1Api", lambda: api)
        monkeypatch.setattr(service_discovery, "CLUSTER_DOMAIN", "cluster.local")
        result = service_discovery.find_service_url("app=prometheus")

    expected = (
        "http://prometheus.monitoring.svc.cluster.local:9090" if has_service else None
    )
    assert result == expected
    assert parse_qs(requests[0]["url"].query) == {"labelSelector": ["app=prometheus"]}
    assert requests[0]["headers"]["Authorization"] == "Bearer test-token"
    assert "Error finding url" not in caplog.text


@pytest.mark.parametrize("api_server", [True], indirect=True)
@pytest.mark.parametrize(
    "ca_source", ["bundle", "data", "file", "untrusted", "wrong_explicit"]
)
def test_kubeconfig_preserves_certifi_fallback_and_explicit_ca(
    api_server, monkeypatch, tmp_path, ca_source
):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (200, _services([]))
    ca_file = tmp_path / "ca.crt"
    custom_ca = base64.b64encode(ca_file.read_bytes()).decode("ascii")
    bundle = tmp_path / "certifi.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))
    monkeypatch.setenv("WEBSOCKET_CLIENT_CA_BUNDLE", str(bundle))
    kubeconfig = _kubeconfig(host, {"token": "test-token"})
    if ca_source == "data":
        kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"] = custom_ca
    elif ca_source == "file":
        kubeconfig["clusters"][0]["cluster"]["certificate-authority"] = str(ca_file)
    elif ca_source == "wrong_explicit":
        wrong_ca = tmp_path / "wrong-ca.crt"
        generate_self_signed_cert(str(wrong_ca), str(tmp_path / "wrong-ca.key"))
        kubeconfig["clusters"][0]["cluster"]["certificate-authority"] = str(wrong_ca)
        assert cert_utils.add_custom_certificate(custom_ca)
    elif ca_source == "bundle":
        assert cert_utils.add_custom_certificate(custom_ca)
    config_file = tmp_path / "kubeconfig.json"
    config_file.write_text(json.dumps(kubeconfig), encoding="utf-8")
    original_configuration = client.Configuration.get_default_copy()
    try:
        load_kube_config_with_certifi(config_file=str(config_file))
        with client.ApiClient() as api_client:
            configuration = api_client.configuration
            assert configuration.verify_ssl is True
            if ca_source in ("data", "file"):
                assert (
                    Path(configuration.ssl_ca_cert).read_bytes() == ca_file.read_bytes()
                )
                assert configuration.ssl_ca_cert != str(bundle)
            elif ca_source == "wrong_explicit":
                assert configuration.ssl_ca_cert == str(wrong_ca)
            else:
                assert configuration.ssl_ca_cert == str(bundle)
            api = client.CoreV1Api(api_client)
            if ca_source in ("untrusted", "wrong_explicit"):
                with pytest.raises(
                    (ApiException, MaxRetryError), match="CERTIFICATE_VERIFY_FAILED"
                ):
                    api.list_service_for_all_namespaces()
                assert requests == []
                return
            result = api.list_service_for_all_namespaces()
    finally:
        client.Configuration.set_default(original_configuration)

    assert isinstance(result, client.V1ServiceList)
    assert result.items == []
    assert requests[0]["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.parametrize("api_server", [True], indirect=True)
@pytest.mark.parametrize("entrypoint", ["service_discovery", "operator"])
@pytest.mark.parametrize("ca_source", ["bundle", "data", "file"])
async def test_kubeconfig_startup_entrypoints_preserve_custom_ca(
    api_server, monkeypatch, tmp_path, entrypoint, ca_source
):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (
        200,
        _services(
            [
                {
                    "metadata": {"name": "prometheus", "namespace": "monitoring"},
                    "spec": {"ports": [{"port": 9090}]},
                }
            ]
        ),
    )
    path = "/apis/holmesgpt.dev/v1alpha1/namespaces/default/healthchecks"
    routes["GET", path] = (200, {"items": []})
    bundle = tmp_path / "certifi.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))
    monkeypatch.setenv("WEBSOCKET_CLIENT_CA_BUNDLE", str(bundle))
    custom_ca = base64.b64encode((tmp_path / "ca.crt").read_bytes()).decode("ascii")
    kubeconfig = _kubeconfig(host, {"token": "test-token"})
    cluster = kubeconfig["clusters"][0]["cluster"]
    if ca_source == "data":
        cluster["certificate-authority-data"] = custom_ca
    elif ca_source == "file":
        cluster["certificate-authority"] = str(tmp_path / "ca.crt")
    else:
        assert cert_utils.add_custom_certificate(custom_ca)
    config_file = tmp_path / "kubeconfig.json"
    config_file.write_text(json.dumps(kubeconfig), encoding="utf-8")
    monkeypatch.setattr(
        config.kube_config, "KUBE_CONFIG_DEFAULT_LOCATION", str(config_file)
    )
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    original_configuration = client.Configuration.get_default_copy()
    try:
        if entrypoint == "service_discovery":
            importlib.reload(service_discovery)
            assert service_discovery.find_service_url("app=prometheus") is not None
        else:
            monkeypatch.setattr(
                config,
                "load_incluster_config",
                MagicMock(side_effect=config.ConfigException("not in a pod")),
            )
            scheduler = MagicMock(start=AsyncMock(), stop=AsyncMock())
            monkeypatch.setattr(
                operator_context, "SchedulerManager", MagicMock(return_value=scheduler)
            )
            monkeypatch.setattr(
                operator_context,
                "HolmesAPIClient",
                MagicMock(return_value=MagicMock(close=AsyncMock())),
            )
            for name in ("config", "api_client", "k8s_api", "scheduler_manager"):
                monkeypatch.setattr(operator_context, name, None)
            try:
                await operator_context.initialize()
                result = operator_context.k8s_api.list_namespaced_custom_object(
                    group="holmesgpt.dev",
                    version="v1alpha1",
                    namespace="default",
                    plural="healthchecks",
                )
                assert result == {"items": []}
                scheduler.start.assert_awaited_once()
            finally:
                await operator_context.cleanup()
        assert requests[0]["headers"]["Authorization"] == "Bearer test-token"
        ca_path = client.Configuration.get_default_copy().ssl_ca_cert
        if ca_source == "bundle":
            assert ca_path == str(bundle)
        else:
            assert ca_path != str(bundle)
            assert Path(ca_path).read_bytes() == (tmp_path / "ca.crt").read_bytes()
    finally:
        client.Configuration.set_default(original_configuration)


@pytest.mark.parametrize("use_exec", [False, True])
def test_kubeconfig_authentication_reaches_the_api(api_server, use_exec):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (200, _services([]))
    credential = {
        "apiVersion": "client.authentication.k8s.io/v1beta1",
        "kind": "ExecCredential",
        "status": {"token": "test-token"},
    }
    user = (
        {
            "exec": {
                "apiVersion": credential["apiVersion"],
                "command": sys.executable,
                "args": ["-c", f"print({json.dumps(credential)!r})"],
            }
        }
        if use_exec
        else {"token": "test-token"}
    )
    with client.ApiClient(_kube_configuration(host, user)) as api_client:
        client.CoreV1Api(api_client).list_service_for_all_namespaces()
    assert requests[0]["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.parametrize("api_server", [True], indirect=True)
def test_incluster_authentication_refreshes_projected_token(
    api_server, monkeypatch, tmp_path
):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (200, _services([]))
    token_file = tmp_path / "token"
    ca_file = tmp_path / "ca.crt"
    token_file.write_text("OrIgInAl-ToKeN")
    server_url = urlsplit(host)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", server_url.hostname)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", str(server_url.port))
    monkeypatch.setattr(
        config.incluster_config, "SERVICE_TOKEN_FILENAME", str(token_file)
    )
    monkeypatch.setattr(config.incluster_config, "SERVICE_CERT_FILENAME", str(ca_file))
    configuration = client.Configuration()

    with freeze_time("2026-01-01") as clock:
        config.load_incluster_config(client_configuration=configuration)
        assert configuration.ssl_ca_cert == str(ca_file)
        assert configuration.verify_ssl is True
        with client.ApiClient(configuration) as api_client:
            api = client.CoreV1Api(api_client)
            api.list_service_for_all_namespaces()
            token_file.write_text("RoTaTeD-ToKeN")
            clock.tick(timedelta(minutes=2))
            api.list_service_for_all_namespaces()

    headers = [r["headers"]["Authorization"].split(" ", 1) for r in requests]
    assert [scheme.casefold() for scheme, _ in headers] == ["bearer", "bearer"]
    assert [token for _, token in headers] == ["OrIgInAl-ToKeN", "RoTaTeD-ToKeN"]


def test_legacy_authorization_key_still_authenticates(api_server):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (200, _services([]))
    configuration = client.Configuration(host=host)
    configuration.api_key["authorization"] = "Bearer legacy-token"
    with client.ApiClient(configuration) as api_client:
        client.CoreV1Api(api_client).list_service_for_all_namespaces()
    assert requests[0]["headers"]["Authorization"] == "Bearer legacy-token"


@pytest.mark.parametrize("log_text", ["ready caf\u00e9\n", '{"status":"ready"}\n'])
@pytest.mark.parametrize("interrupt_first_read", [False, True])
def test_self_logs_deserializes_pods_and_reads_plain_text(
    api_server, monkeypatch, log_text, interrupt_first_read
):
    host, routes, requests = api_server
    pod = {
        "metadata": {
            "name": "holmes-0",
            "namespace": "default",
            "labels": {"app": "holmes"},
        },
        "spec": {"containers": [{"name": "holmes"}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "holmes",
                    "image": "holmes:test",
                    "imageID": "test-image",
                    "ready": True,
                    "restartCount": 2,
                    "state": {"running": {}},
                }
            ],
        },
    }
    routes["GET", "/api/v1/namespaces/default/pods/holmes-0"] = (200, pod)
    routes["GET", "/api/v1/namespaces/default/pods"] = (
        200,
        {"apiVersion": "v1", "kind": "PodList", "items": [pod]},
    )
    log_path = "/api/v1/namespaces/default/pods/holmes-0/log"
    routes["GET", log_path] = (200, log_text)
    if interrupt_first_read:

        def _interrupt_body(handler):
            routes["GET", log_path] = (200, log_text)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/plain")
            handler.send_header("Content-Length", "100")
            handler.end_headers()
            handler.wfile.write(b"partial\n")
            handler.close_connection = True

        routes["GET", log_path] = _interrupt_body
    monkeypatch.setattr(self_logs, "_detect_runner_namespace", lambda: "default")
    monkeypatch.setattr(self_logs, "detect_pod_name", lambda: "holmes-0")
    with client.ApiClient(
        _kube_configuration(host, {"token": "test-token"})
    ) as api_client:
        api = client.CoreV1Api(api_client)
        result = self_logs.get_holmes_logs(
            self_logs.HolmesLogsRequest(tail_lines=20, previous=True),
            core_api_factory=lambda: api,
        )

    assert result["error"] is None
    assert result["pods"][0]["ready"] is True
    assert result["pods"][0]["restarts"] == 2
    assert result["pods"][0]["containers"][0]["state"] == "running"
    assert result["logs"][0]["text"] == log_text
    assert result["logs"][0]["error"] is None
    log_requests = [r for r in requests if r["url"].path == log_path]
    assert len(log_requests) == (2 if interrupt_first_read else 1)
    query = parse_qs(requests[-1]["url"].query)
    assert query["container"] == ["holmes"]
    assert query["previous"][0].lower() == "true"
    assert query["tailLines"] == ["20"]


def test_self_logs_preserves_rbac_errors(api_server, monkeypatch):
    host, routes, _ = api_server
    routes["GET", "/api/v1/namespaces/default/pods/holmes-0"] = (
        403,
        {"kind": "Status", "status": "Failure", "reason": "Forbidden", "code": 403},
    )
    monkeypatch.setattr(self_logs, "_detect_runner_namespace", lambda: "default")
    monkeypatch.setattr(self_logs, "detect_pod_name", lambda: "holmes-0")
    with client.ApiClient(
        _kube_configuration(host, {"token": "test-token"})
    ) as api_client:
        api = client.CoreV1Api(api_client)
        result = self_logs.get_holmes_logs(
            self_logs.HolmesLogsRequest(), core_api_factory=lambda: api
        )
    assert "HTTP 403 Forbidden" in result["error"]
    assert result["pods"] == []


async def test_operator_updates_status_with_the_sync_client(api_server):
    host, routes, requests = api_server
    path = "/apis/holmesgpt.dev/v1alpha1/namespaces/default/healthchecks/test/status"
    routes["PATCH", path] = (200, {"status": {"phase": "Running"}})
    with client.ApiClient(
        _kube_configuration(host, {"token": "test-token"})
    ) as api_client:
        await update_healthcheck_status(
            client.CustomObjectsApi(api_client),
            "test",
            "default",
            phase=CheckPhase.RUNNING,
        )
    assert requests[0]["method"] == "PATCH"
    assert requests[0]["body"] == {"status": {"phase": "Running"}}
    assert requests[0]["headers"]["Authorization"] == "Bearer test-token"


async def test_native_async_client_can_make_awaitable_requests(api_server):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (
        200,
        _services([{"metadata": {"name": "prometheus", "namespace": "monitoring"}}]),
    )
    configuration = aio_client.Configuration(host=host)
    configuration.api_key["BearerToken"] = "Bearer async-token"
    async with aio_client.ApiClient(configuration) as api_client:
        services = await aio_client.CoreV1Api(
            api_client
        ).list_service_for_all_namespaces(label_selector="app=prometheus")
    assert services.items[0].metadata.name == "prometheus"
    assert requests[0]["headers"]["Authorization"] == "Bearer async-token"
    assert parse_qs(requests[0]["url"].query) == {"labelSelector": ["app=prometheus"]}
