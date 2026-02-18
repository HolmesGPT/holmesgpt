import logging
import os
import threading
from typing import List, Optional

CLUSTER_DOMAIN = os.environ.get("CLUSTER_DOMAIN", "cluster.local")

# Lazy-load kubernetes config to avoid the heavy import cost (~seconds) at
# module level.  The kubernetes package pulls in dozens of sub-modules and
# load_incluster_config / load_kube_config perform file I/O.  Deferring this
# to first use keeps the server startup fast so health probes are not blocked.
_kube_config_loaded = False
_kube_config_lock = threading.Lock()


def _ensure_kube_config():
    global _kube_config_loaded
    if _kube_config_loaded:
        return
    with _kube_config_lock:
        if _kube_config_loaded:
            return
        from kubernetes import config  # type: ignore

        try:
            if os.getenv("KUBERNETES_SERVICE_HOST"):
                config.load_incluster_config()
            else:
                config.load_kube_config()
        except config.config_exception.ConfigException as e:
            logging.warning(f"Running without kube-config! e={e}")
        _kube_config_loaded = True


def find_service_url(label_selector):
    """
    Get the url of an in-cluster service with a specific label
    """
    _ensure_kube_config()

    from kubernetes import client  # type: ignore

    # we do it this way because there is a weird issue with hikaru's ServiceList.listServiceForAllNamespaces()
    try:
        v1 = client.CoreV1Api()
        svc_list = v1.list_service_for_all_namespaces(
            label_selector=label_selector
        )
        if not svc_list.items:
            return None
        svc = svc_list.items[0]
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
