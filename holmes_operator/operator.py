#!/usr/bin/env python3
"""Holmes Kubernetes Operator entry point."""

import logging
import sys
from typing import Any

import kopf

from holmes_operator import config as operator_config
from holmes_operator import context

# Import handlers to register them with kopf
from holmes_operator.handlers import healthcheck  # noqa: F401
from holmes_operator.handlers import scheduledhealthcheck  # noqa: F401
from holmes_operator.handlers import triggeredhealthcheck  # noqa: F401

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

    # Initialize context (loads config, k8s client, Holmes API client, scheduler)
    await context.initialize()

    logger.info("Holmes Operator started successfully")

    # Configure kopf settings
    settings.persistence.finalizer = "holmesgpt.dev/operator"
    settings.posting.enabled = True  # Enable event posting
    settings.watching.connect_timeout = 1 * 60  # 1 minute
    settings.watching.server_timeout = 10 * 60  # 10 minutes

    if operator_config.HOLMES_OPERATOR_NAMESPACE:
        # Namespace-scoped mode: drop kopf's cluster-scoped requirements so the
        # operator runs with a namespaced Role only (no ClusterRole).
        # - scanning.disabled stops kopf from watching namespaces & CRDs
        #   cluster-wide; it serves the explicit namespace passed to kopf.run().
        # - peering.standalone avoids the cluster-scoped ClusterKopfPeering
        #   resource (single-replica operator needs no peer coordination).
        settings.scanning.disabled = True
        settings.peering.standalone = True


@kopf.on.cleanup()
async def cleanup_handler(**kwargs) -> None:
    logger.info("Shutting down Holmes Operator...")
    await context.cleanup()
    logger.info("Holmes Operator shut down successfully")


def main() -> None:
    """Run the operator, scoped to a single namespace or cluster-wide.

    Uses ``kopf.run(namespaces=[ns])`` when ``HOLMES_OPERATOR_NAMESPACE`` is set,
    otherwise ``kopf.run(clusterwide=True)``.
    """
    try:
        namespace = operator_config.HOLMES_OPERATOR_NAMESPACE
        if namespace:
            logger.info(f"Running namespace-scoped in namespace '{namespace}'")
            kopf.run(namespaces=[namespace])
        else:
            logger.info("Running cluster-wide")
            kopf.run(clusterwide=True)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error in operator: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
