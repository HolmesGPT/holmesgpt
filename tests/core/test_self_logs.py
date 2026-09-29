"""Holmes's own pods/logs (the ``holmes_logs`` internal request kind)."""

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from kubernetes.client import (
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1ObjectMeta,
    V1Pod,
    V1PodCondition,
    V1PodList,
    V1PodSpec,
    V1PodStatus,
)
from kubernetes.client import V1Container
from kubernetes.client.rest import ApiException

from holmes.core import self_logs
from holmes.core.self_logs import (
    HolmesLogsRequest,
    RingBufferLogHandler,
    get_holmes_logs,
    install_memory_log_handler,
    label_selector_for,
)

NS = "robusta"
OWN = "robusta-holmes-7d9f-abcde"
OTHER = "robusta-holmes-7d9f-fghij"


@pytest.fixture(autouse=True)
def _pod_env(monkeypatch, tmp_path):
    monkeypatch.setenv("POD_NAMESPACE", NS)
    monkeypatch.setenv("HOSTNAME", OWN)
    monkeypatch.setattr(self_logs, "_SERVICEACCOUNT_NAMESPACE_FILE", tmp_path / "absent")
    handler = RingBufferLogHandler(capacity=5)
    monkeypatch.setattr(self_logs, "_memory_handler", handler)
    return handler


def _pod(name, labels=None, restarts=0, ready=True, containers=("holmes",), last_reason=None):
    statuses = []
    for c in containers:
        statuses.append(
            V1ContainerStatus(
                name=c,
                ready=ready,
                restart_count=restarts,
                image="img",
                image_id="id",
                state=V1ContainerState(running=V1ContainerStateRunning())
                if ready
                else V1ContainerState(waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff")),
                last_state=V1ContainerState(
                    terminated=V1ContainerStateTerminated(exit_code=137, reason=last_reason)
                )
                if last_reason
                else V1ContainerState(),
            )
        )
    return V1Pod(
        metadata=V1ObjectMeta(name=name, namespace=NS, labels=labels if labels is not None else {"app": "holmes"}),
        spec=V1PodSpec(containers=[V1Container(name=c) for c in containers]),
        status=V1PodStatus(
            phase="Running",
            start_time=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            conditions=[V1PodCondition(type="Ready", status="True" if ready else "False")],
            container_statuses=statuses,
        ),
    )


def _api(pods, logs=None, own=None):
    api = MagicMock()
    api.read_namespaced_pod.return_value = own or pods[0]
    api.list_namespaced_pod.return_value = V1PodList(items=list(pods))

    def _read_log(name, namespace, container, tail_lines, previous, **_):
        value = (logs or {}).get((name, container), f"{name}/{container} line\n")
        if isinstance(value, Exception):
            raise value
        return value

    api.read_namespaced_pod_log.side_effect = _read_log
    return api


def test_request_defaults_and_clamp():
    assert HolmesLogsRequest().model_dump() == {
        "tail_lines": 500,
        "previous": False,
        "pod_name": None,
        "include_logs": True,
    }
    assert HolmesLogsRequest(tail_lines=0).tail_lines == 1
    assert HolmesLogsRequest(tail_lines=-5).tail_lines == 1
    assert HolmesLogsRequest(tail_lines=10_000).tail_lines == 5000
    assert HolmesLogsRequest(tail_lines=None).tail_lines == 500


@pytest.mark.parametrize(
    "labels, expected",
    [
        (
            {"app.kubernetes.io/name": "holmes", "app.kubernetes.io/instance": "r1", "app": "holmes"},
            "app.kubernetes.io/name=holmes,app.kubernetes.io/instance=r1",
        ),
        ({"app.kubernetes.io/name": "holmes", "app": "holmes"}, "app=holmes"),
        ({"app": "holmes", "pod-template-hash": "x"}, "app=holmes"),
        ({"pod-template-hash": "x"}, None),
    ],
)
def test_label_selector(labels, expected):
    assert label_selector_for(labels) == expected


def test_kubernetes_pods_and_logs():
    own = _pod(OWN)
    other = _pod(OTHER, restarts=3, ready=False, last_reason="OOMKilled")
    api = _api([other, own], own=own)

    data = get_holmes_logs(HolmesLogsRequest(tail_lines=100, previous=True), core_api_factory=lambda: api)

    assert data["source"] == "kubernetes"
    assert data["namespace"] == NS
    assert data["error"] is None
    api.read_namespaced_pod.assert_called_once_with(name=OWN, namespace=NS)
    api.list_namespaced_pod.assert_called_once_with(namespace=NS, label_selector="app=holmes")

    assert [p["name"] for p in data["pods"]] == [OWN, OTHER]
    assert data["pods"][0] == {
        "name": OWN,
        "phase": "Running",
        "ready": True,
        "restarts": 0,
        "start_time": "2026-09-01T12:00:00+00:00",
        "containers": [
            {
                "name": "holmes",
                "ready": True,
                "restart_count": 0,
                "state": "running",
                "state_reason": None,
                "last_state_reason": None,
            }
        ],
    }
    crashed = data["pods"][1]
    assert crashed["ready"] is False and crashed["restarts"] == 3
    assert crashed["containers"][0]["state"] == "waiting"
    assert crashed["containers"][0]["state_reason"] == "CrashLoopBackOff"
    assert crashed["containers"][0]["last_state_reason"] == "OOMKilled"

    assert data["logs"] == [
        {"pod": OWN, "container": "holmes", "previous": True, "text": f"{OWN}/holmes line\n", "truncated": False, "error": None},
        {"pod": OTHER, "container": "holmes", "previous": True, "text": f"{OTHER}/holmes line\n", "truncated": False, "error": None},
    ]
    for call in api.read_namespaced_pod_log.call_args_list:
        assert call.kwargs["tail_lines"] == 100 and call.kwargs["previous"] is True


def test_include_logs_false_skips_log_reads():
    api = _api([_pod(OWN)])
    data = get_holmes_logs(HolmesLogsRequest(include_logs=False), core_api_factory=lambda: api)
    assert data["source"] == "kubernetes"
    assert data["logs"] == []
    assert len(data["pods"]) == 1
    api.read_namespaced_pod_log.assert_not_called()


def test_pod_name_filter_restricts_logs_but_not_pods():
    api = _api([_pod(OWN), _pod(OTHER)])
    data = get_holmes_logs(HolmesLogsRequest(pod_name=OTHER), core_api_factory=lambda: api)
    assert len(data["pods"]) == 2
    assert [entry["pod"] for entry in data["logs"]] == [OTHER]


def test_pod_name_outside_holmes_pods_is_refused():
    api = _api([_pod(OWN)])
    data = get_holmes_logs(HolmesLogsRequest(pod_name="kube-apiserver"), core_api_factory=lambda: api)
    assert data["logs"] == []
    assert "not a Holmes pod" in data["error"]
    api.read_namespaced_pod_log.assert_not_called()


def test_no_usable_labels_reports_own_pod_only():
    own = _pod(OWN, labels={})
    api = _api([own], own=own)
    data = get_holmes_logs(HolmesLogsRequest(), core_api_factory=lambda: api)
    api.list_namespaced_pod.assert_not_called()
    assert [p["name"] for p in data["pods"]] == [OWN]


def test_previous_log_missing_is_reported_per_container():
    api = _api(
        [_pod(OWN), _pod(OTHER)],
        logs={(OWN, "holmes"): ApiException(status=400, reason="Bad Request")},
    )
    data = get_holmes_logs(HolmesLogsRequest(previous=True), core_api_factory=lambda: api)
    assert data["source"] == "kubernetes"
    own_entry = next(e for e in data["logs"] if e["pod"] == OWN)
    assert own_entry["text"] == ""
    assert "HTTP 400" in own_entry["error"] and "previous logs" in own_entry["error"]
    other_entry = next(e for e in data["logs"] if e["pod"] == OTHER)
    assert other_entry["error"] is None


def test_total_output_is_bounded(monkeypatch):
    monkeypatch.setattr(self_logs, "MAX_TOTAL_LOG_CHARS", 1000)
    big = "".join(f"line {i:05d}\n" for i in range(1000))
    api = _api([_pod(OWN), _pod(OTHER)], logs={(OWN, "holmes"): big, (OTHER, "holmes"): "short\n"})
    data = get_holmes_logs(HolmesLogsRequest(), core_api_factory=lambda: api)
    total = sum(len(e["text"]) for e in data["logs"])
    assert total <= 1000
    own_entry = next(e for e in data["logs"] if e["pod"] == OWN)
    assert own_entry["truncated"] is True
    # Newest lines survive, cut on a line boundary.
    assert own_entry["text"].endswith("line 00999\n")
    assert own_entry["text"].startswith("line ")
    other_entry = next(e for e in data["logs"] if e["pod"] == OTHER)
    assert other_entry == {**other_entry, "text": "short\n", "truncated": False}


# ---- memory fallback ----


def _fill_memory(handler, n):
    logger = logging.getLogger("holmes-self-logs-test")
    handler.setFormatter(logging.Formatter("%(message)s"))
    for i in range(n):
        handler.handle(logger.makeRecord(logger.name, logging.INFO, __file__, 0, f"mem {i}", None, None))


def test_forbidden_own_pod_read_falls_back_to_memory(_pod_env):
    _fill_memory(_pod_env, 8)
    api = MagicMock()
    api.read_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    data = get_holmes_logs(HolmesLogsRequest(tail_lines=3), core_api_factory=lambda: api)
    assert data["source"] == "memory"
    assert data["namespace"] == NS
    assert data["pods"] == []
    assert "HTTP 403 Forbidden" in data["error"] and OWN in data["error"]
    assert data["logs"] == [
        {"pod": OWN, "container": "holmes", "previous": False, "text": "mem 5\nmem 6\nmem 7", "truncated": False, "error": None}
    ]


def test_memory_buffer_is_bounded(_pod_env):
    _fill_memory(_pod_env, 50)
    assert _pod_env.tail(100) == [f"mem {i}" for i in range(45, 50)]


def test_no_incluster_config_falls_back_to_memory():
    def _boom():
        raise RuntimeError("Service host/port is not set.")

    data = get_holmes_logs(HolmesLogsRequest(previous=True), core_api_factory=_boom)
    assert data["source"] == "memory"
    assert "Service host/port is not set" in data["error"]
    assert "previous container logs are unavailable" in data["error"]


def test_outside_kubernetes_falls_back_to_memory(monkeypatch):
    monkeypatch.delenv("POD_NAMESPACE")
    factory = MagicMock()
    data = get_holmes_logs(HolmesLogsRequest(), core_api_factory=factory)
    assert data["source"] == "memory"
    assert data["namespace"] is None
    factory.assert_not_called()


def test_namespace_from_service_account_file(monkeypatch, tmp_path):
    monkeypatch.delenv("POD_NAMESPACE")
    ns_file = tmp_path / "namespace"
    ns_file.write_text("holmes-ns\n")
    monkeypatch.setattr(self_logs, "_SERVICEACCOUNT_NAMESPACE_FILE", ns_file)
    api = _api([_pod(OWN)])
    data = get_holmes_logs(HolmesLogsRequest(include_logs=False), core_api_factory=lambda: api)
    assert data["namespace"] == "holmes-ns"
    api.read_namespaced_pod.assert_called_once_with(name=OWN, namespace="holmes-ns")


def test_forbidden_log_reads_fall_back_to_memory_keeping_pods(_pod_env):
    _fill_memory(_pod_env, 2)
    forbidden = ApiException(status=403, reason="Forbidden")
    api = _api([_pod(OWN)], logs={(OWN, "holmes"): forbidden})
    data = get_holmes_logs(HolmesLogsRequest(), core_api_factory=lambda: api)
    assert data["source"] == "memory"
    assert [p["name"] for p in data["pods"]] == [OWN]
    assert data["logs"][0]["text"] == "mem 0\nmem 1"
    assert "pods/log" in data["error"]


def test_memory_fallback_with_other_pod_name_returns_no_logs(_pod_env):
    _fill_memory(_pod_env, 2)
    data = get_holmes_logs(
        HolmesLogsRequest(pod_name=OTHER),
        core_api_factory=MagicMock(side_effect=RuntimeError("no config")),
    )
    assert data["source"] == "memory"
    assert data["logs"] == []
    assert OTHER in data["error"]


def test_install_memory_log_handler_is_idempotent(monkeypatch):
    monkeypatch.setattr(self_logs, "_memory_handler", None)
    root = logging.getLogger()
    try:
        first = install_memory_log_handler(capacity=10)
        second = install_memory_log_handler(capacity=10)
        assert first is second
        assert root.handlers.count(first) == 1
        logging.getLogger("x").warning("captured-line")
        assert any("captured-line" in line for line in first.tail(10))
    finally:
        root.removeHandler(self_logs._memory_handler)
