"""Global operator context for sharing state across handlers."""

import asyncio
import logging
from typing import Optional

from kubernetes import client
from kubernetes import config as k8s_config

from holmes_operator import reaper
from holmes_operator.client.holmes_api_client import HolmesAPIClient
from holmes_operator.config import OperatorConfig
from holmes_operator.scheduler.manager import SchedulerManager

logger = logging.getLogger(__name__)

# Global operator state (initialized during startup)
config: Optional[OperatorConfig] = None
api_client: Optional[HolmesAPIClient] = None
k8s_api: Optional[client.CustomObjectsApi] = None
scheduler_manager: Optional[SchedulerManager] = None
reaper_task: Optional[asyncio.Task] = None


async def initialize() -> OperatorConfig:
    """
    Initialize global operator context.

    This should be called once during operator startup. Loads operator
    configuration, Kubernetes configuration (in-cluster or kubeconfig),
    creates the Kubernetes API client, and initializes the Holmes API client.

    Returns:
        OperatorConfig: The loaded operator configuration

    Side Effects:
        Sets global variables: config, api_client, k8s_api, and scheduler_manager
    """
    global config, api_client, k8s_api, scheduler_manager, reaper_task

    # Load operator configuration
    config = OperatorConfig.load()
    logger.info(
        f"Loaded configuration: Holmes API URL={config.holmes_api_url}, "
        f"Log Level={config.log_level}"
    )

    # Update log level from config
    logging.getLogger().setLevel(config.log_level)

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
        base_url=config.holmes_api_url,
        timeout=config.holmes_api_timeout,
    )

    # Initialize and start scheduler manager
    scheduler_manager = SchedulerManager(
        timezone_str="UTC",
        k8s_api=k8s_api,
    )
    await scheduler_manager.start()

    # Start the completed HealthCheck reaper (no-op task=None when disabled)
    reaper_task = reaper.start_reaper(k8s_api, config)

    return config


async def cleanup() -> None:
    """
    Cleanup global operator context.
    """
    global api_client, k8s_api, scheduler_manager, reaper_task
    if reaper_task is not None:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        reaper_task = None
    if api_client is not None:
        await api_client.close()
    if k8s_api is not None:
        k8s_api.api_client.close()
    if scheduler_manager is not None:
        await scheduler_manager.stop()
