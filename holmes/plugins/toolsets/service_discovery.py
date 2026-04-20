import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from kubernetes import client  # type: ignore
from kubernetes import config  # type: ignore
from kubernetes.client import V1ServiceList  # type: ignore
from kubernetes.client.models.v1_service import V1Service  # type: ignore

CLUSTER_DOMAIN = os.environ.get("CLUSTER_DOMAIN", "cluster.local")

_is_running_in_cluster = bool(os.getenv("KUBERNETES_SERVICE_HOST"))

try:
    if _is_running_in_cluster:
        config.load_incluster_config()
    else:
        config.load_kube_config()
except config.config_exception.ConfigException as e:
    logging.warning(f"Running without kube-config! e={e}")


@dataclass
class DiscoveredService:
    """Structured representation of a discovered Kubernetes service."""

    name: str
    namespace: str
    port: int


def is_running_in_cluster() -> bool:
    """Check whether HolmesGPT is running inside a Kubernetes cluster."""
    return _is_running_in_cluster


def build_service_url(service: DiscoveredService) -> str:
    """
    Build a reachable URL for a discovered Kubernetes service.

    When running in-cluster, returns the standard cluster DNS URL.
    When running locally (outside the cluster), returns a K8s API proxy URL
    that routes through the API server using kubeconfig auth.
    """
    if is_running_in_cluster():
        return f"http://{service.name}.{service.namespace}.svc.{CLUSTER_DOMAIN}:{service.port}"

    # Running locally — use the K8s API server proxy to reach the service.
    # The kubernetes python client's Configuration already has the api server host
    # from the loaded kubeconfig.
    api_client = client.ApiClient()
    api_host = api_client.configuration.host.rstrip("/")
    return f"{api_host}/api/v1/namespaces/{service.namespace}/services/{service.name}:{service.port}/proxy/"


def find_service(label_selector: str) -> Optional[DiscoveredService]:
    """
    Find a Kubernetes service matching a label selector.

    Returns a DiscoveredService with name, namespace, and port,
    or None if no matching service is found.
    """
    try:
        v1 = client.CoreV1Api()
        svc_list: V1ServiceList = v1.list_service_for_all_namespaces(  # type: ignore
            label_selector=label_selector
        )
        if not svc_list.items:
            return None
        svc: V1Service = svc_list.items[0]  # type: ignore
        name = svc.metadata.name
        namespace = svc.metadata.namespace
        port = svc.spec.ports[0].port
        logging.info(
            f"Discovered service with label-selector: `{label_selector}` at {namespace}/{name}:{port}"
        )
        return DiscoveredService(name=name, namespace=namespace, port=port)
    except Exception:
        logging.warning("Error finding service", exc_info=True)
        return None


def find_service_url(label_selector: str) -> Optional[str]:
    """
    Get the url of a Kubernetes service with a specific label.

    When running in-cluster, returns the standard cluster DNS URL.
    When running locally, returns a K8s API proxy URL.
    """
    service = find_service(label_selector)
    if service is None:
        return None
    url = build_service_url(service)
    logging.info(
        f"Discovered service with label-selector: `{label_selector}` at url: `{url}`"
    )
    return url


def resolve_kubernetes_service(kubernetes_service: str) -> Optional[DiscoveredService]:
    """
    Resolve a 'namespace/service_name' string to a DiscoveredService by looking up the
    service in the Kubernetes API to determine the port.

    Args:
        kubernetes_service: Service reference in 'namespace/service_name' format.

    Returns:
        DiscoveredService if found, None otherwise.
    """
    parts = kubernetes_service.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        logging.error(
            f"Invalid kubernetes_service format: '{kubernetes_service}'. "
            "Expected 'namespace/service_name' (e.g. 'monitoring/prometheus-server')"
        )
        return None

    namespace, service_name = parts
    try:
        v1 = client.CoreV1Api()
        svc: V1Service = v1.read_namespaced_service(
            name=service_name, namespace=namespace
        )  # type: ignore
        port = svc.spec.ports[0].port
        logging.info(
            f"Resolved kubernetes_service '{kubernetes_service}' with port {port}"
        )
        return DiscoveredService(name=service_name, namespace=namespace, port=port)
    except Exception:
        logging.warning(
            f"Failed to resolve kubernetes_service '{kubernetes_service}'",
            exc_info=True,
        )
        return None


class ServiceDiscovery:
    @classmethod
    def find_url(cls, selectors: List[str], error_msg: str) -> Optional[str]:
        """
        Try to autodiscover the url of an in-cluster service
        """

        for label_selector in selectors:
            service_url = find_service_url(label_selector)
            if service_url:
                return service_url

        logging.debug(error_msg)
        return None


class PrometheusDiscovery(ServiceDiscovery):
    @classmethod
    def find_prometheus_url(cls) -> Optional[str]:
        return super().find_url(
            selectors=[
                "app=kube-prometheus-stack-prometheus",
                "app=prometheus,component=server,release!=kubecost",
                "app=prometheus-server",
                "app=prometheus-operator-prometheus",
                "app=rancher-monitoring-prometheus",
                "app=prometheus-prometheus",
                "app.kubernetes.io/component=query,app.kubernetes.io/name=thanos",
                "app.kubernetes.io/name=thanos-query",
                "app=thanos-query",
                "app=thanos-querier",
            ],
            error_msg="Prometheus url could not be found. Add 'prometheus_url' under your prometheus tools config",
        )

    @classmethod
    def find_vm_url(cls) -> Optional[str]:
        return super().find_url(
            selectors=[
                "app.kubernetes.io/name=vmsingle",
                "app.kubernetes.io/name=victoria-metrics-single",
                "app.kubernetes.io/name=vmselect",
                "app=vmselect",
            ],
            error_msg="Victoria Metrics url could not be found. Add 'prometheus_url' under your prometheus tools config",
        )
