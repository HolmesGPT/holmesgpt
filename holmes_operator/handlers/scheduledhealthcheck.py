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

    Args:
        spec: ScheduledHealthCheck spec
        name: Resource name
        namespace: Resource namespace
        uid: Resource UID
        logger: Kopf logger
    """
    logger.info(f"Creating ScheduledHealthCheck: {namespace}/{name}")

    try:
        # Parse and validate spec
        scheduled_spec = ScheduledHealthCheckSpec(**spec)

        # Set initial condition
        condition = HealthCheckCondition(
            type="ScheduleRegistered",
            status=ConditionStatus.TRUE
            if scheduled_spec.enabled
            else ConditionStatus.FALSE,
            lastTransitionTime=get_current_time_iso(),
            reason="Created" if scheduled_spec.enabled else "Disabled",
            message=f"Schedule '{scheduled_spec.schedule}' "
            + ("registered" if scheduled_spec.enabled else "not registered (disabled)"),
        )

        await _add_scheduledhealthcheck_condition(
            api=context.k8s_api, name=name, namespace=namespace, condition=condition
        )

        # Register with scheduler if enabled
        if scheduled_spec.enabled:
            try:
                await context.scheduler_manager.add_schedule(
                    name=name,
                    namespace=namespace,
                    cron_expr=scheduled_spec.schedule,
                    spec=scheduled_spec,
                    scheduled_uid=uid,
                )
                logger.info(
                    f"Registered schedule for {namespace}/{name}: {scheduled_spec.schedule}"
                )
            except ValueError as e:
                # Invalid cron expression
                logger.error(f"Invalid cron expression for {namespace}/{name}: {e}")
                error_condition = HealthCheckCondition(
                    type="ScheduleRegistered",
                    status=ConditionStatus.FALSE,
                    lastTransitionTime=get_current_time_iso(),
                    reason="InvalidCron",
                    message=f"Invalid cron expression: {str(e)}",
                )
                await _add_scheduledhealthcheck_condition(
                    api=context.k8s_api,
                    name=name,
                    namespace=namespace,
                    condition=error_condition,
                )
                raise
        else:
            logger.info(f"Schedule {namespace}/{name} created but disabled")

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
                # Enabled
                await context.scheduler_manager.add_schedule(
                    name=name,
                    namespace=namespace,
                    cron_expr=new_spec.schedule,
                    spec=new_spec,
                    scheduled_uid=uid,
                )
                logger.info(f"Enabled schedule for {namespace}/{name}")

                condition = HealthCheckCondition(
                    type="ScheduleRegistered",
                    status=ConditionStatus.TRUE,
                    lastTransitionTime=get_current_time_iso(),
                    reason="Enabled",
                    message=f"Schedule '{new_spec.schedule}' registered",
                )
                await _add_scheduledhealthcheck_condition(
                    api=context.k8s_api,
                    name=name,
                    namespace=namespace,
                    condition=condition,
                )
            else:
                # Disabled
                await context.scheduler_manager.remove_schedule(
                    name=name, namespace=namespace
                )
                logger.info(f"Disabled schedule for {namespace}/{name}")

                condition = HealthCheckCondition(
                    type="ScheduleRegistered",
                    status=ConditionStatus.FALSE,
                    lastTransitionTime=get_current_time_iso(),
                    reason="Disabled",
                    message="Schedule disabled",
                )
                await _add_scheduledhealthcheck_condition(
                    api=context.k8s_api,
                    name=name,
                    namespace=namespace,
                    condition=condition,
                )

        # Handle schedule or spec changes (when still enabled)
        elif new_spec.enabled and spec_changed:
            await context.scheduler_manager.update_schedule(
                name=name,
                namespace=namespace,
                cron_expr=new_spec.schedule,
                spec=new_spec,
                scheduled_uid=uid,
            )
            logger.info(f"Updated schedule for {namespace}/{name}")

            condition = HealthCheckCondition(
                type="ScheduleRegistered",
                status=ConditionStatus.TRUE,
                lastTransitionTime=get_current_time_iso(),
                reason="Updated",
                message=f"Schedule updated to '{new_spec.schedule}'",
            )
            await _add_scheduledhealthcheck_condition(
                api=context.k8s_api, name=name, namespace=namespace, condition=condition
            )

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
    """
    Handle ScheduledHealthCheck deletion.

    Removes the schedule from SchedulerManager. Active HealthChecks will be
    cleaned up automatically via ownerReferences.

    Args:
        name: Resource name
        namespace: Resource namespace
        logger: Kopf logger
    """
    logger.info(f"Deleting ScheduledHealthCheck: {namespace}/{name}")

    try:
        await context.scheduler_manager.remove_schedule(name=name, namespace=namespace)
        logger.info(f"Removed schedule for {namespace}/{name}")

    except Exception as e:
        logger.error(
            f"Failed to delete ScheduledHealthCheck {namespace}/{name}: {e}",
            exc_info=True,
        )
        raise


# Helper function (will be moved to utils.py in Step 5)


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
