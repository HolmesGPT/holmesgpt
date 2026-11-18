"""Exercise Kubernetes SDK compatibility against an isolated HTTP API stub."""

import json
import ssl
import sys
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from freezegun import freeze_time
from kubernetes import client, config
from kubernetes.aio import client as aio_client

from holmes.core import self_logs
from holmes.plugins.toolsets import service_discovery
from holmes_operator.models import CheckPhase
from holmes_operator.utils import update_healthcheck_status
from tests.plugins.toolsets.test_prometheus_ssl import generate_self_signed_cert


@pytest.fixture
def api_server(request, tmp_path):
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
            status, payload = routes.get(
                (self.command, urlsplit(self.path).path),
                (404, {"message": "Unexpected Kubernetes API request"}),
            )
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


def _kube_configuration(host, user):
    configuration = client.Configuration()
    config.load_kube_config_from_dict(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": "test",
            "contexts": [
                {"name": "test", "context": {"cluster": "test", "user": "test"}}
            ],
            "clusters": [{"name": "test", "cluster": {"server": host}}],
            "users": [{"name": "test", "user": user}],
        },
        client_configuration=configuration,
        persist_config=False,
    )
    return configuration


def _services(items):
    return {"apiVersion": "v1", "kind": "ServiceList", "items": items}


@pytest.mark.parametrize("has_service", [True, False])
def test_service_discovery_uses_generated_service_models(
    api_server, monkeypatch, has_service
):
    host, routes, requests = api_server
    service = {
        "metadata": {"name": "prometheus", "namespace": "monitoring"},
        "spec": {"ports": [{"port": 9090}]},
    }
    routes["GET", "/api/v1/services"] = (
        200, _services([service] if has_service else [])
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
    token_file.write_text("original-token")
    server_url = urlsplit(host)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", server_url.hostname)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", str(server_url.port))
    monkeypatch.setattr(config.incluster_config, "SERVICE_TOKEN_FILENAME", str(token_file))
    monkeypatch.setattr(config.incluster_config, "SERVICE_CERT_FILENAME", str(ca_file))
    configuration = client.Configuration()

    with freeze_time("2026-01-01") as clock:
        config.load_incluster_config(client_configuration=configuration)
        assert configuration.ssl_ca_cert == str(ca_file)
        assert configuration.verify_ssl is True
        with client.ApiClient(configuration) as api_client:
            api = client.CoreV1Api(api_client)
            api.list_service_for_all_namespaces()
            token_file.write_text("rotated-token")
            clock.tick(timedelta(minutes=2))
            api.list_service_for_all_namespaces()

    assert [r["headers"]["Authorization"].casefold() for r in requests] == [
        "bearer original-token",
        "bearer rotated-token",
    ]


def test_legacy_authorization_key_still_authenticates(api_server):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (200, _services([]))
    configuration = client.Configuration(host=host)
    configuration.api_key["authorization"] = "Bearer legacy-token"
    with client.ApiClient(configuration) as api_client:
        client.CoreV1Api(api_client).list_service_for_all_namespaces()
    assert requests[0]["headers"]["Authorization"] == "Bearer legacy-token"


@pytest.mark.parametrize("log_text", ["ready caf\u00e9\n", '{"status":"ready"}\n'])
def test_self_logs_deserializes_pods_and_reads_plain_text(
    api_server, monkeypatch, log_text
):
    host, routes, requests = api_server
    pod = {
        "metadata": {
            "name": "holmes-0", "namespace": "default", "labels": {"app": "holmes"}
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
        200, {"apiVersion": "v1", "kind": "PodList", "items": [pod]}
    )
    routes["GET", "/api/v1/namespaces/default/pods/holmes-0/log"] = (200, log_text)
    monkeypatch.setattr(self_logs, "_detect_runner_namespace", lambda: "default")
    monkeypatch.setattr(self_logs, "detect_pod_name", lambda: "holmes-0")
    with client.ApiClient(_kube_configuration(host, {"token": "test-token"})) as api_client:
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
    query = parse_qs(requests[-1]["url"].query)
    assert query["container"] == ["holmes"]
    assert query["previous"][0].lower() == "true"
    assert query["tailLines"] == ["20"]


def test_self_logs_preserves_rbac_errors(api_server, monkeypatch):
    host, routes, _ = api_server
    routes["GET", "/api/v1/namespaces/default/pods/holmes-0"] = (
        403, {"kind": "Status", "status": "Failure", "reason": "Forbidden", "code": 403}
    )
    monkeypatch.setattr(self_logs, "_detect_runner_namespace", lambda: "default")
    monkeypatch.setattr(self_logs, "detect_pod_name", lambda: "holmes-0")
    with client.ApiClient(_kube_configuration(host, {"token": "test-token"})) as api_client:
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
    with client.ApiClient(_kube_configuration(host, {"token": "test-token"})) as api_client:
        await update_healthcheck_status(
            client.CustomObjectsApi(api_client), "test", "default", phase=CheckPhase.RUNNING
        )
    assert requests[0]["method"] == "PATCH"
    assert requests[0]["body"] == {"status": {"phase": "Running"}}
    assert requests[0]["headers"]["Authorization"] == "Bearer test-token"


async def test_native_async_client_can_make_awaitable_requests(api_server):
    host, routes, requests = api_server
    routes["GET", "/api/v1/services"] = (
        200, _services([{"metadata": {"name": "prometheus", "namespace": "monitoring"}}])
    )
    configuration = aio_client.Configuration(host=host)
    configuration.api_key["BearerToken"] = "Bearer async-token"
    async with aio_client.ApiClient(configuration) as api_client:
        services = await aio_client.CoreV1Api(api_client).list_service_for_all_namespaces(
            label_selector="app=prometheus"
        )
    assert services.items[0].metadata.name == "prometheus"
    assert requests[0]["headers"]["Authorization"] == "Bearer async-token"
    assert parse_qs(requests[0]["url"].query) == {"labelSelector": ["app=prometheus"]}
