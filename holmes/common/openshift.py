import logging
import os
from functools import lru_cache
from typing import Optional

# NOTE: This one will be mounted if openshift is enabled in values.yaml
TOKEN_LOCATION = os.environ.get(
    "TOKEN_LOCATION", "/var/run/secrets/kubernetes.io/serviceaccount/token"
)

# OpenShift Prometheus defaults
OPENSHIFT_PROMETHEUS_URL = "https://prometheus-k8s.openshift-monitoring.svc:9091"
OPENSHIFT_THANOS_URL = "https://thanos-querier.openshift-monitoring.svc:9091"


def load_openshift_token() -> Optional[str]:
    try:
        with open(TOKEN_LOCATION, "r") as file:
            return file.read()
    except FileNotFoundError:
        return None


@lru_cache(maxsize=1)
def detect_openshift() -> bool:
    """
    Auto-detect if running on OpenShift by checking for OpenShift-specific API groups.

    Returns True if OpenShift is detected, False otherwise.
    Result is cached for performance.
    """
    # Only attempt detection if running in a Kubernetes cluster
    if not os.getenv("KUBERNETES_SERVICE_HOST"):
        return False

    try:
        from kubernetes import client, config

        # Load in-cluster config
        config.load_incluster_config()

        # Check for OpenShift-specific API groups
        api_client = client.ApiClient()
        api_instance = client.ApisApi(api_client)
        api_groups = api_instance.get_api_versions()

        openshift_api_groups = {
            "route.openshift.io",
            "apps.openshift.io",
            "project.openshift.io",
            "config.openshift.io",
        }

        for group in api_groups.groups:
            if group.name in openshift_api_groups:
                logging.info(f"OpenShift detected via API group: {group.name}")
                return True

        return False

    except Exception as e:
        logging.debug(f"OpenShift auto-detection failed: {e}")
        return False


def is_openshift_cluster() -> bool:
    """
    Check if running on OpenShift.

    Returns True if:
    - IS_OPENSHIFT environment variable is set to true (explicit flag), OR
    - OpenShift is auto-detected via API groups

    The explicit flag always takes precedence for override purposes.
    """
    from holmes.common.env_vars import load_bool

    # Check explicit flag first
    explicit_flag = load_bool("IS_OPENSHIFT", None)
    if explicit_flag is not None:
        if explicit_flag:
            logging.debug(
                "OpenShift mode enabled via IS_OPENSHIFT environment variable"
            )
        return explicit_flag

    # Fall back to auto-detection
    detected = detect_openshift()
    if detected:
        logging.info("OpenShift auto-detected - enabling OpenShift mode")
    return detected
