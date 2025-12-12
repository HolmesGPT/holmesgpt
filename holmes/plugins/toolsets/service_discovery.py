import logging
import os
from typing import List, Optional

from kubernetes import client  # type: ignore
from kubernetes import config  # type: ignore
from kubernetes.client import V1ServiceList  # type: ignore
from kubernetes.client.models.v1_service import V1Service  # type: ignore

CLUSTER_DOMAIN = os.environ.get("CLUSTER_DOMAIN", "cluster.local")

try:
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
    else:
        config.load_kube_config()
except config.config_exception.ConfigException as e:
    logging.warning(f"Running without kube-config! e={e}")


def find_service_url(label_selector):
    """
    Get the url of an in-cluster service with a specific label
    """
    # we do it this way because there is a weird issue with hikaru's ServiceList.listServiceForAllNamespaces()
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
        url = f"http://{name}.{namespace}.svc.{CLUSTER_DOMAIN}:{port}"
        logging.info(
            f"Discovered service with label-selector: `{label_selector}` at url: `{url}`"
        )
        return url
    except Exception:
        logging.warning("Error finding url")
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
