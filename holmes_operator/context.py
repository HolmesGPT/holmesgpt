"""Global operator context for sharing state across handlers."""

from typing import Optional

from kubernetes import client

from holmes_operator.client.holmes_api_client import HolmesAPIClient
from holmes_operator.config import OperatorConfig

# Global operator state (initialized in operator.py)
config: Optional[OperatorConfig] = None
api_client: Optional[HolmesAPIClient] = None
k8s_api: Optional[client.CustomObjectsApi] = None


def initialize(
    cfg: OperatorConfig,
    api: HolmesAPIClient,
    k8s: client.CustomObjectsApi,
) -> None:
    """
    Initialize global operator context.

    This should be called once during operator startup.

    Args:
        cfg: Operator configuration
        api: Holmes API HTTP client
        k8s: Kubernetes CustomObjectsApi client
    """
    global config, api_client, k8s_api
    config = cfg
    api_client = api
    k8s_api = k8s
