"""Global operator context for sharing state across handlers."""

from typing import TYPE_CHECKING, Optional

from kubernetes import client

from holmes_operator.client.holmes_api_client import HolmesAPIClient
from holmes_operator.config import OperatorConfig

if TYPE_CHECKING:
    from holmes_operator.scheduler.manager import SchedulerManager

# Global operator state (initialized in operator.py)
config: Optional[OperatorConfig] = None
api_client: Optional[HolmesAPIClient] = None
k8s_api: Optional[client.CustomObjectsApi] = None
scheduler_manager: Optional["SchedulerManager"] = None


def initialize(
    cfg: OperatorConfig,
    api: HolmesAPIClient,
    k8s: client.CustomObjectsApi,
    scheduler: "SchedulerManager",
):
    """
    Initialize global operator context.

    This should be called once during operator startup.

    Args:
        cfg: Operator configuration
        api: Holmes API HTTP client
        k8s: Kubernetes CustomObjectsApi client
        scheduler: Scheduler manager for recurring checks
    """
    global config, api_client, k8s_api, scheduler_manager
    config = cfg
    api_client = api
    k8s_api = k8s
    scheduler_manager = scheduler
