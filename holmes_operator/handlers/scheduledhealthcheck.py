"""
Kopf handlers for ScheduledHealthCheck CRD.

Handles lifecycle events for ScheduledHealthCheck resources:
- Creation: Register schedule with SchedulerManager
- Updates: Handle schedule/enabled changes
- Deletion: Remove schedule from SchedulerManager
"""

import asyncio
import logging
from typing import Any, Dict

import kopf

from holmes_operator import context
from holmes_operator.models import (
    ConditionStatus,
    HealthCheckCondition,
    ScheduledHealthCheckConditionType,
    ScheduledHealthCheckSpec,
)
from holmes_operator.utils import get_current_time_iso

logger = logging.getLogger(__name__)


@kopf.on.create("holmesgpt.dev", "v1alpha1", "scheduledhealthchecks")  # type: ignore[arg-type]
async def on_scheduledhealthcheck_create(
    *,
    spec: Dict[str, Any],
    name: str,
    namespace: str,
    uid: str,
    logger: kopf.Logger,
    **kwargs,
):
    """
    Handle ScheduledHealthCheck creation.

    Validates the spec, registers the schedule with SchedulerManager if enabled,
    and sets initial status condition.
    """
    logger.info(f"Creating ScheduledHealthCheck: {namespace}/{name}")
    try:
        scheduled_spec = ScheduledHealthCheckSpec(**spec)

        if scheduled_spec.enabled:
            await _register_scheduled_healthcheck(name, namespace, uid, scheduled_spec)
        else:
            await _unregister_scheduled_healthcheck(name, namespace, unschedule=False)
    except Exception as e:
        logger.error(
            f"Failed to create ScheduledHealthCheck {namespace}/{name}: {e}",
            exc_info=True,
        )
        raise


@kopf.on.update("holmesgpt.dev", "v1alpha1", "scheduledhealthchecks")  # type: ignore[arg-type]
async def on_scheduledhealthcheck_update(
    *,
    old: Dict[str, Any],
    new: Dict[str, Any],
    name: str,
    namespace: str,
    uid: str,
    logger: kopf.Logger,
    **kwargs,
):
    """
    Handle ScheduledHealthCheck updates.

    Monitors changes to schedule-related fields:
    - schedule (cron expression)
    - enabled (on/off toggle)
    - query, timeout, mode, destinations

    Args:
        old: Previous resource state
        new: New resource state
        name: Resource name
        namespace: Resource namespace
        uid: Resource UID
        logger: Kopf logger
    """
    old_spec_dict = old.get("spec", {})
    new_spec_dict = new.get("spec", {})

    try:
        old_spec = ScheduledHealthCheckSpec(**old_spec_dict)
        new_spec = ScheduledHealthCheckSpec(**new_spec_dict)

        # Check what changed
        schedule_changed = old_spec.schedule != new_spec.schedule
        enabled_changed = old_spec.enabled != new_spec.enabled
        spec_changed = (
            schedule_changed
            or old_spec.query != new_spec.query
            or old_spec.timeout != new_spec.timeout
            or old_spec.mode != new_spec.mode
            or old_spec.model != new_spec.model
            or old_spec.destinations != new_spec.destinations
        )

        logger.info(
            f"Updating ScheduledHealthCheck {namespace}/{name}: "
            f"schedule_changed={schedule_changed}, enabled_changed={enabled_changed}, spec_changed={spec_changed}"
        )

        # Handle enable/disable toggle
        if enabled_changed:
            if new_spec.enabled:
                await _register_scheduled_healthcheck(name, namespace, uid, new_spec)
            else:
                await _unregister_scheduled_healthcheck(name, namespace)

        # Handle schedule or spec changes (when still enabled)
        elif new_spec.enabled and spec_changed:
            await _update_scheduled_healthcheck(name, namespace, uid, new_spec)

    except Exception as e:
        logger.error(
            f"Failed to update ScheduledHealthCheck {namespace}/{name}: {e}",
            exc_info=True,
        )
        raise


@kopf.on.delete("holmesgpt.dev", "v1alpha1", "scheduledhealthchecks")  # type: ignore[arg-type]
async def on_scheduledhealthcheck_delete(
    *,
    name: str,
    namespace: str,
    logger: kopf.Logger,
    **kwargs,
):
    logger.info(f"Deleting ScheduledHealthCheck: {namespace}/{name}")
    await _unregister_scheduled_healthcheck(name, namespace)


async def _update_scheduled_healthcheck(
    name: str, namespace: str, uid: str, spec: ScheduledHealthCheckSpec
):
    logger.info(f"Updating schedule for {namespace}/{name}")
    await context.scheduler_manager.update_schedule(
        name=name,
        namespace=namespace,
        cron_expr=spec.schedule,
        spec=spec,
        scheduled_uid=uid,
    )

    await set_scheduledhealthcheck_condition(
        api=context.k8s_api,
        name=name,
        namespace=namespace,
        condition_type=ScheduledHealthCheckConditionType.SCHEDULE_REGISTERED,
        status=ConditionStatus.TRUE,
        reason="Updated",
        message=f"Schedule updated to '{spec.schedule}'",
    )


async def _unregister_scheduled_healthcheck(
    name: str, namespace: str, unschedule: bool = True
):
    logger.info(
        f"Unregistering schedule for {namespace}/{name}, unscheduling={unschedule}"
    )
    if unschedule:
        await context.scheduler_manager.remove_schedule(name=name, namespace=namespace)
    await set_scheduledhealthcheck_condition(
        api=context.k8s_api,
        name=name,
        namespace=namespace,
        condition_type=ScheduledHealthCheckConditionType.SCHEDULE_REGISTERED,
        status=ConditionStatus.FALSE,
        reason="Disabled",
        message="Schedule unregistered successfully",
    )


async def _register_scheduled_healthcheck(
    name: str, namespace: str, uid: str, spec: ScheduledHealthCheckSpec
):
    logger.info(f"Registering schedule for {namespace}/{name}")
    try:
        await context.scheduler_manager.add_schedule(
            name=name,
            namespace=namespace,
            cron_expr=spec.schedule,
            spec=spec,
            scheduled_uid=uid,
        )
        await set_scheduledhealthcheck_condition(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            condition_type=ScheduledHealthCheckConditionType.SCHEDULE_REGISTERED,
            status=ConditionStatus.TRUE,
            reason="Registered",
            message=f"Schedule '{spec.schedule}' registered successfully",
        )
    except ValueError as e:
        logger.error(f"Invalid cron expression for {namespace}/{name}: {e}")
        await set_scheduledhealthcheck_condition(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            condition_type=ScheduledHealthCheckConditionType.SCHEDULE_REGISTERED,
            status=ConditionStatus.FALSE,
            reason="InvalidCron",
            message=f"Invalid cron expression: {str(e)}",
        )
        raise
    except Exception as e:
        logger.error(
            f"Failed to register schedule for {namespace}/{name}: {e}", exc_info=True
        )
        await set_scheduledhealthcheck_condition(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            condition_type=ScheduledHealthCheckConditionType.SCHEDULE_REGISTERED,
            status=ConditionStatus.FALSE,
            reason="InternalError",
            message=str(e),
        )
        raise


async def set_scheduledhealthcheck_condition(
    api,
    name: str,
    namespace: str,
    condition_type: ScheduledHealthCheckConditionType,
    status: ConditionStatus,
    reason: str,
    message: str,
) -> None:
    """
    Set a condition on a ScheduledHealthCheck resource.

    Creates a HealthCheckCondition and updates the resource status.

    Args:
        api: Kubernetes CustomObjectsApi
        name: ScheduledHealthCheck name
        namespace: ScheduledHealthCheck namespace
        condition_type: Type of condition (from ScheduledHealthCheckConditionType enum)
        status: Condition status (True/False/Unknown)
        reason: Short machine-readable reason
        message: Human-readable message
    """
    condition = HealthCheckCondition(
        type=condition_type,
        status=status,
        lastTransitionTime=get_current_time_iso(),
        reason=reason,
        message=message,
    )
    await _add_scheduledhealthcheck_condition(
        api=api, name=name, namespace=namespace, condition=condition
    )


async def _add_scheduledhealthcheck_condition(
    api, name: str, namespace: str, condition: HealthCheckCondition
):
    """Add or update condition in ScheduledHealthCheck status."""
    try:
        # Get current resource
        resource = await asyncio.to_thread(
            api.get_namespaced_custom_object,
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="scheduledhealthchecks",
            name=name,
        )

        status = resource.get("status", {})
        conditions = status.get("conditions", [])

        # Find existing condition of same type
        existing_idx = None
        for idx, cond in enumerate(conditions):
            if cond.get("type") == condition.type:
                existing_idx = idx
                break

        # Update or append condition
        condition_dict = {
            "type": condition.type,
            "status": condition.status.value,
            "lastTransitionTime": condition.lastTransitionTime,
            "reason": condition.reason,
            "message": condition.message,
        }

        if existing_idx is not None:
            conditions[existing_idx] = condition_dict
        else:
            conditions.append(condition_dict)

        # Patch status
        await asyncio.to_thread(
            api.patch_namespaced_custom_object_status,
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="scheduledhealthchecks",
            name=name,
            body={"status": {"conditions": conditions}},
        )

    except Exception as e:
        logger.error(f"Failed to add condition: {e}", exc_info=True)
