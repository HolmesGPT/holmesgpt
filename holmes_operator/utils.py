"""Utility functions for operator."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client

from holmes_operator.models import (
    CheckPhase,
    CheckStatus,
    HealthCheckCondition,
    NotificationStatus,
)

logger = logging.getLogger(__name__)


async def update_healthcheck_status(
    api: client.CustomObjectsApi,
    name: str,
    namespace: str,
    phase: Optional[CheckPhase] = None,
    result: Optional[CheckStatus] = None,
    message: Optional[str] = None,
    rationale: Optional[str] = None,
    duration: Optional[float] = None,
    error: Optional[str] = None,
    model_used: Optional[str] = None,
    notifications: Optional[List[NotificationStatus]] = None,
    start_time: Optional[str] = None,
    completion_time: Optional[str] = None,
) -> None:
    """
    Update HealthCheck status fields.

    Args:
        api: Kubernetes CustomObjectsApi instance
        name: Name of the HealthCheck resource
        namespace: Namespace of the HealthCheck resource
        phase: Execution phase (Pending, Running, Completed, Failed)
        result: Check result (pass, fail, error)
        message: Human-readable summary
        rationale: LLM explanation
        duration: Execution duration in seconds
        error: Error details
        model_used: Model that was used
        notifications: List of notification statuses
        start_time: ISO format start time
        completion_time: ISO format completion time
    """
    try:
        # Build status update
        status: Dict[str, Any] = {}

        if phase is not None:
            status["phase"] = phase.value
        if result is not None:
            status["result"] = result.value
        if message is not None:
            status["message"] = message
        if rationale is not None:
            status["rationale"] = rationale
        if duration is not None:
            status["duration"] = duration
        if error is not None:
            status["error"] = error
        if model_used is not None:
            status["modelUsed"] = model_used
        if notifications is not None:
            status["notifications"] = [n.model_dump() for n in notifications]
        if start_time is not None:
            status["startTime"] = start_time
        if completion_time is not None:
            status["completionTime"] = completion_time

        # Update status subresource
        api.patch_namespaced_custom_object_status(
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="healthchecks",
            name=name,
            body={"status": status},
        )

        logger.debug(
            f"Updated HealthCheck status: {namespace}/{name}",
            extra={
                "name": name,
                "namespace": namespace,
                "phase": phase,
                "result": result,
            },
        )

    except Exception as e:
        logger.error(
            f"Failed to update HealthCheck status: {namespace}/{name}",
            exc_info=True,
            extra={"name": name, "namespace": namespace, "error": str(e)},
        )
        raise


async def add_healthcheck_condition(
    api: client.CustomObjectsApi,
    name: str,
    namespace: str,
    condition: HealthCheckCondition,
) -> None:
    """
    Add or update a condition in HealthCheck status.

    Args:
        api: Kubernetes CustomObjectsApi instance
        name: Name of the HealthCheck resource
        namespace: Namespace of the HealthCheck resource
        condition: The condition to add or update
    """
    try:
        # Get current resource to read existing conditions
        resource = api.get_namespaced_custom_object(
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="healthchecks",
            name=name,
        )

        # Get existing conditions or initialize empty list
        conditions = resource.get("status", {}).get("conditions", [])

        # Find existing condition of this type
        existing_condition = None
        for i, cond in enumerate(conditions):
            if cond.get("type") == condition.type:
                existing_condition = i
                break

        # Update or append condition
        if existing_condition is not None:
            conditions[existing_condition] = condition.model_dump()
        else:
            conditions.append(condition.model_dump())

        # Update status with new conditions
        api.patch_namespaced_custom_object_status(
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="healthchecks",
            name=name,
            body={"status": {"conditions": conditions}},
        )

        logger.debug(
            f"Added condition to HealthCheck: {namespace}/{name}",
            extra={
                "name": name,
                "namespace": namespace,
                "condition_type": condition.type,
                "status": condition.status.value,
            },
        )

    except Exception as e:
        logger.error(
            f"Failed to add condition to HealthCheck: {namespace}/{name}",
            exc_info=True,
            extra={
                "name": name,
                "namespace": namespace,
                "condition_type": condition.type,
                "error": str(e),
            },
        )
        raise


def get_current_time_iso() -> str:
    """Get current time in ISO 8601 format with timezone."""
    return datetime.now(timezone.utc).isoformat()


async def set_healthcheck_pending(
    api: client.CustomObjectsApi,
    name: str,
    namespace: str,
) -> None:
    """Set HealthCheck status to Pending."""
    await update_healthcheck_status(
        api=api,
        name=name,
        namespace=namespace,
        phase=CheckPhase.PENDING,
        start_time=get_current_time_iso(),
    )


async def set_healthcheck_running(
    api: client.CustomObjectsApi,
    name: str,
    namespace: str,
) -> None:
    """Set HealthCheck status to Running."""
    await update_healthcheck_status(
        api=api,
        name=name,
        namespace=namespace,
        phase=CheckPhase.RUNNING,
    )


async def set_healthcheck_completed(
    api: client.CustomObjectsApi,
    name: str,
    namespace: str,
    result: CheckStatus,
    message: str,
    rationale: Optional[str] = None,
    duration: Optional[float] = None,
    error: Optional[str] = None,
    model_used: Optional[str] = None,
    notifications: Optional[List[NotificationStatus]] = None,
) -> None:
    """Set HealthCheck status to Completed with result details."""
    await update_healthcheck_status(
        api=api,
        name=name,
        namespace=namespace,
        phase=CheckPhase.COMPLETED,
        result=result,
        message=message,
        rationale=rationale,
        duration=duration,
        error=error,
        model_used=model_used,
        notifications=notifications,
        completion_time=get_current_time_iso(),
    )


async def set_healthcheck_failed(
    api: client.CustomObjectsApi,
    name: str,
    namespace: str,
    message: str,
    error: str,
) -> None:
    """Set HealthCheck status to Failed due to operator error."""
    await update_healthcheck_status(
        api=api,
        name=name,
        namespace=namespace,
        phase=CheckPhase.FAILED,
        result=CheckStatus.ERROR,
        message=message,
        error=error,
        completion_time=get_current_time_iso(),
    )
