#!/usr/bin/env python3
"""Holmes Kubernetes Operator entry point."""

import logging
import sys
from typing import Any

import kopf
from kubernetes import client
from kubernetes import config as k8s_config

from holmes_operator import context
from holmes_operator.client.holmes_api_client import HolmesAPIClient
from holmes_operator.config import OperatorConfig

# Import handlers to register them with kopf
from holmes_operator.handlers import healthcheck  # noqa: F401

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@kopf.on.startup()
async def startup_handler(settings: kopf.OperatorSettings, **kwargs: Any) -> None:
    """
    Initialize operator on startup.

    This runs once when the operator starts and initializes all global state.
    """
    logger.info("Starting Holmes Operator...")

    # Load operator configuration
    operator_config = OperatorConfig.load()
    logger.info(
        f"Loaded configuration: Holmes API URL={operator_config.holmes_api_url}, "
        f"Log Level={operator_config.log_level}"
    )

    # Update log level from config
    logging.getLogger().setLevel(operator_config.log_level)

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
        base_url=operator_config.holmes_api_url,
        timeout=operator_config.holmes_api_timeout,
    )

    # Initialize global context
    context.initialize(
        cfg=operator_config,
        api=api_client,
        k8s=k8s_api,
    )

    logger.info("Holmes Operator started successfully")

    # Configure kopf settings
    settings.persistence.finalizer = "holmesgpt.dev/operator"
    settings.posting.enabled = True  # Enable event posting
    settings.watching.connect_timeout = 1 * 60  # 1 minute
    settings.watching.server_timeout = 10 * 60  # 10 minutes


@kopf.on.cleanup()
async def cleanup_handler(**kwargs) -> None:
    """
    Cleanup resources on operator shutdown.
    """
    logger.info("Shutting down Holmes Operator...")

    # Close API client
    if context.api_client:
        await context.api_client.close()

    logger.info("Holmes Operator shut down successfully")


def main() -> None:
    """Main entry point for the operator."""
    try:
        # Run the operator
        # kopf.run is blocking and handles the event loop
        kopf.run(
            clusterwide=True,  # Watch all namespaces
            liveness_endpoint="http://0.0.0.0:8080/healthz",  # Health check endpoint
        )
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error in operator: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
