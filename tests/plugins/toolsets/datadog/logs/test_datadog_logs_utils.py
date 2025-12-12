from pydantic import AnyUrl
import pytest

from holmes.plugins.toolsets.datadog.toolset_datadog_logs import (
    DatadogLogsConfig,
    calculate_page_size,
    format_logs,
)
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams


@pytest.mark.parametrize(
    "params, configs, logs, expected_page_size",
    [
        (
            FetchPodLogsParams(pod_name="*", namespace="*"),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [],
            300,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*"),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [0] * 900,
            100,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*"),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=10,
            ),
            [0] * 900,
            10,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*", limit=950),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [0] * 900,
            50,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*", limit=950),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=10,
            ),
            [0] * 900,
            10,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*"),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [0] * 1000,
            0,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*", limit=100),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [0] * 100,
            0,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*"),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [0] * 1200,
            0,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*", limit=50),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [],
            50,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*", limit=1),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1000,
                page_size=300,
            ),
            [],
            1,
        ),
        (
            FetchPodLogsParams(pod_name="*", namespace="*"),
            DatadogLogsConfig(
                dd_api_key="xyz",
                dd_app_key="xyz",
                site_api_url=AnyUrl("https://example.com"),
                default_limit=1,
                page_size=300,
            ),
            [],
            1,
        ),
    ],
)
def test_calculate_page_size(params, configs, logs, expected_page_size):
    assert calculate_page_size(params, configs, logs) == expected_page_size


@pytest.mark.parametrize(
    "raw_logs, expected_logs",
    [
        (
            [
                {
                    "id": "AwAAAZgNLCz85___jAAAABhBWmdOTERNSkFBQjh1a3FVWURvX1V3RGsAAAAkMDE5ODBkMzQtN2FlOC00YzhlLWIxZTMtNTRkOWJjODFmMjE1AAPZkQ",
                    "type": "log",
                    "attributes": {
                        "service": "multiple-errors-in-logs",
                        "host": "kind-double-node-worker",
                        "message": "2025-07-15T08:20:54.879Z [INFO] inventory-service - Auth event: login_failed - User: user_1081 - IP: 192.168.52.128 - RequestID: n5e6lw8ueh",
                        "status": "info",
                        "timestamp": "2025-07-15T08:20:55.676Z",
                        "tags": [
                            "container_id:0fbd68bb3ed1a1c3818ec465bc8c51b864d2472394eb9c219616d0ad7670fd6b",
                            "container_name:main-container",
                            "datadog.pipelines:false",
                            "datadog.submission_auth:api_key",
                            "dirname:/var/log/pods/ask-holmes-namespace-51_my-app-51-59d94fd7cc-927wn_1708abd3-8fbc-41ac-8dcf-de2254a04e8a/main-container",
                            "display_container_name:main-container_my-app-51-59d94fd7cc-927wn",
                            "env:dev",
                            "filename:17.log",
                            "image_id:us-central1-docker.pkg.dev/genuine-flight-317411/devel/multiple-errors-in-logs@sha256:b2ae4b0b1add7b6b13e55397930a42b75f696368b659cb47582be5c6dfdd0e2a",
                            "image_name:us-central1-docker.pkg.dev/genuine-flight-317411/devel/multiple-errors-in-logs",
                            "image_tag:v1",
                            "kube_container_name:main-container",
                            "kube_deployment:my-app-51",
                            "kube_namespace:ask-holmes-namespace-51",
                            "kube_node:kind-double-node-worker",
                            "kube_ownerref_kind:replicaset",
                            "kube_ownerref_name:my-app-51-59d94fd7cc",
                            "kube_qos:burstable",
                            "kube_replica_set:my-app-51-59d94fd7cc",
                            "orch_cluster_id:ac9adc60-b5fe-4818-9241-443fdb4e8556",
                            "pod_name:my-app-51-59d94fd7cc-927wn",
                            "pod_phase:running",
                            "service:multiple-errors-in-logs",
                            "short_image:multiple-errors-in-logs",
                            "source:multiple-errors-in-logs",
                        ],
                    },
                },
                {
                    "id": "AwAAAZgNLCz85___kwAAABhBWmdOTERNSkFBQjh1a3FVWURvX1V3RHIAAAAkMDE5ODBkMzQtN2FlOC00YzhlLWIxZTMtNTRkOWJjODFmMjE1AAPZmA",
                    "type": "log",
                    "attributes": {
                        "service": "multiple-errors-in-logs",
                        "host": "kind-double-node-worker",
                        "message": "2025-07-15T08:20:55.236Z [INFO] notification-service - Auth event: login_failed - User: user_1001 - IP: 192.168.234.119 - RequestID: j0smuzixk8",
                        "status": "info",
                        "timestamp": "2025-07-15T08:20:55.676Z",
                        "tags": [
                            "container_id:0fbd68bb3ed1a1c3818ec465bc8c51b864d2472394eb9c219616d0ad7670fd6b",
                            "container_name:main-container",
                            "datadog.pipelines:false",
                            "datadog.submission_auth:api_key",
                            "dirname:/var/log/pods/ask-holmes-namespace-51_my-app-51-59d94fd7cc-927wn_1708abd3-8fbc-41ac-8dcf-de2254a04e8a/main-container",
                            "display_container_name:main-container_my-app-51-59d94fd7cc-927wn",
                            "env:dev",
                            "filename:17.log",
                            "image_id:us-central1-docker.pkg.dev/genuine-flight-317411/devel/multiple-errors-in-logs@sha256:b2ae4b0b1add7b6b13e55397930a42b75f696368b659cb47582be5c6dfdd0e2a",
                            "image_name:us-central1-docker.pkg.dev/genuine-flight-317411/devel/multiple-errors-in-logs",
                            "image_tag:v1",
                            "kube_container_name:main-container",
                            "kube_deployment:my-app-51",
                            "kube_namespace:ask-holmes-namespace-51",
                            "kube_node:kind-double-node-worker",
                            "kube_ownerref_kind:replicaset",
                            "kube_ownerref_name:my-app-51-59d94fd7cc",
                            "kube_qos:burstable",
                            "kube_replica_set:my-app-51-59d94fd7cc",
                            "orch_cluster_id:ac9adc60-b5fe-4818-9241-443fdb4e8556",
                            "pod_name:my-app-51-59d94fd7cc-927wn",
                            "pod_phase:running",
                            "service:multiple-errors-in-logs",
                            "short_image:multiple-errors-in-logs",
                            "source:multiple-errors-in-logs",
                        ],
                    },
                },
            ],
            "[2025-07-15T08:20:55.676Z] 2025-07-15T08:20:54.879Z [INFO] inventory-service - Auth event: login_failed - User: user_1081 - IP: 192.168.52.128 - RequestID: n5e6lw8ueh\n[2025-07-15T08:20:55.676Z] 2025-07-15T08:20:55.236Z [INFO] notification-service - Auth event: login_failed - User: user_1001 - IP: 192.168.234.119 - RequestID: j0smuzixk8",
        ),
        (
            [
                {
                    "id": "ABCD",
                    "type": "malformatted log",
                    "random_field": "random_value",
                }
            ],
            '{"id": "ABCD", "type": "malformatted log", "random_field": "random_value"}',
        ),
    ],
)
def test_format_logs(raw_logs, expected_logs):
    assert format_logs(raw_logs) == expected_logs
