#!/usr/bin/env python3
"""Holmes Kubernetes Operator entry point."""

import logging
import sys
from typing import Any

import kopf

from holmes_operator import context
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

    # Initialize global context
    context.initialize(cfg=operator_config)

    # Configure kopf settings
    settings.persistence.finalizer = "holmesgpt.dev/operator"
    settings.posting.enabled = True  # Enable event posting
    settings.watching.connect_timeout = 1 * 60  # 1 minute
    settings.watching.server_timeout = 10 * 60  # 10 minutes

    logger.info("Holmes Operator started successfully")


@kopf.on.cleanup()
async def cleanup_handler(**kwargs) -> None:
    logger.info("Shutting down Holmes Operator...")
    await context.cleanup()
    logger.info("Holmes Operator shut down successfully")


def main() -> None:
    try:
        kopf.run(clusterwide=True)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error in operator: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
