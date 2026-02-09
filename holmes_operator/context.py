"""Global operator context for sharing state across handlers."""

import logging
from typing import Optional

from kubernetes import client
from kubernetes import config as k8s_config

from holmes_operator.client.holmes_api_client import HolmesAPIClient
from holmes_operator.config import OperatorConfig

logger = logging.getLogger(__name__)

# Global operator state (initialized in operator.py)
config: Optional[OperatorConfig] = None
api_client: Optional[HolmesAPIClient] = None
k8s_api: Optional[client.CustomObjectsApi] = None


def initialize(cfg: OperatorConfig) -> None:
    """
    Initialize global operator context.

    This should be called once during operator startup. Loads Kubernetes
    configuration (in-cluster or kubeconfig), creates the Kubernetes API
    client, and initializes the Holmes API client.

    Args:
        cfg: Operator configuration containing Holmes API URL, timeout, and
            other operator settings.

    Side Effects:
        Sets global variables: config, api_client, and k8s_api
    """
    global config, api_client, k8s_api
    config = cfg

    # Initialize Kubernetes client
    try:
        # Try to load in-cluster config first (when running as pod)
        k8s_config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
    except k8s_config.ConfigException:
        # Fall back to kubeconfig (for local development)
        k8s_config.load_kube_config()
        logger.info("Loaded kubeconfig Kubernetes configuration")

    k8s_api = client.CustomObjectsApi()

    # Initialize Holmes API client
    api_client = HolmesAPIClient(
        base_url=cfg.holmes_api_url,
        timeout=cfg.holmes_api_timeout,
    )


async def cleanup() -> None:
    """
    Cleanup global operator context.
    """
    global api_client, k8s_api
    if api_client is not None:
        await api_client.close()
    if k8s_api is not None:
        k8s_api.api_client.close()
