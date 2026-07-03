"""Kopf handlers for the TriggeredHealthCheck CRD (deployment-rollout trigger).

A TriggeredHealthCheck is the event-driven sibling of ScheduledHealthCheck: instead
of a cron schedule, it watches Deployments and spawns a HealthCheck when a matching
Deployment rolls out a new pod template.
"""

import asyncio
import logging
from typing import Any, Dict

import kopf

from holmes_operator import context, trigger_executor
from holmes_operator.models import (
    ConditionStatus,
    HealthCheckCondition,
    TriggeredHealthCheckConditionType,
    TriggeredHealthCheckSpec,
)
from holmes_operator.utils import get_current_time_iso

logger = logging.getLogger(__name__)

GROUP = "holmesgpt.dev"
VERSION = "v1alpha1"


@kopf.on.create(GROUP, VERSION, "triggeredhealthchecks")  # type: ignore[arg-type]
async def on_triggeredhealthcheck_create(
    *,
    spec: Dict[str, Any],
    name: str,
    namespace: str,
    logger: kopf.Logger,
    **kwargs: Any,
) -> None:
    """Validate a TriggeredHealthCheck and mark it Ready (or not)."""
    logger.info(f"Creating TriggeredHealthCheck: {namespace}/{name}")
    await _validate_and_set_ready(spec, name, namespace, logger)


@kopf.on.update(GROUP, VERSION, "triggeredhealthchecks")  # type: ignore[arg-type]
async def on_triggeredhealthcheck_update(
    *,
    new: Dict[str, Any],
    name: str,
    namespace: str,
    logger: kopf.Logger,
    **kwargs: Any,
) -> None:
    """Re-validate on spec changes and refresh the Ready condition."""
    logger.info(f"Updating TriggeredHealthCheck: {namespace}/{name}")
    await _validate_and_set_ready(new.get("spec", {}), name, namespace, logger)


async def _validate_and_set_ready(
    spec: Dict[str, Any], name: str, namespace: str, logger: kopf.Logger
) -> None:
    try:
        parsed = TriggeredHealthCheckSpec(**spec)
    except Exception as e:
        logger.error(f"Invalid TriggeredHealthCheck {namespace}/{name}: {e}")
        await set_triggeredhealthcheck_condition(
            name=name,
            namespace=namespace,
            condition_type=TriggeredHealthCheckConditionType.TRIGGER_FAILED,
            status=ConditionStatus.TRUE,
            reason="InvalidSpec",
            message=str(e),
        )
        raise

    if parsed.enabled:
        reason, message = "Watching", "Watching Deployments for matching rollouts"
        status = ConditionStatus.TRUE
    else:
        reason, message = "Disabled", "Trigger is disabled"
        status = ConditionStatus.FALSE

    await set_triggeredhealthcheck_condition(
        name=name,
        namespace=namespace,
        condition_type=TriggeredHealthCheckConditionType.READY,
        status=status,
        reason=reason,
        message=message,
    )


@kopf.on.event("apps", "v1", "deployments")  # type: ignore[arg-type]
async def on_deployment_event(
    *,
    event: Dict[str, Any],
    body: Dict[str, Any],
    name: str,
    namespace: str,
    meta: Dict[str, Any],
    logger: kopf.Logger,
    **kwargs: Any,
) -> None:
    """Watch Deployments and fan rollouts out to matching TriggeredHealthChecks.

    Uses a low-level event handler (no kopf diff annotations/finalizers are written
    to user Deployments) with an in-memory baseline cache to detect pod-template
    changes.
    """
    key = f"{namespace}/{name}"

    if event.get("type") == "DELETED":
        trigger_executor.forget_deployment(key)
        return

    rollout = trigger_executor.detect_rollout(key, body)
    if rollout is None:
        return  # baseline observation or non-rollout change (status/scale)

    old_image, new_image = rollout
    deployment_labels = meta.get("labels", {}) or {}

    try:
        triggers = await asyncio.to_thread(
            context.k8s_api.list_namespaced_custom_object,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural="triggeredhealthchecks",
        )
    except Exception as e:
        logger.error(f"Failed to list TriggeredHealthChecks in {namespace}: {e}")
        return

    for trigger in triggers.get("items", []):
        trigger_meta = trigger.get("metadata", {})
        trigger_name = trigger_meta.get("name")
        trigger_uid = trigger_meta.get("uid", "")

        try:
            spec = TriggeredHealthCheckSpec(**trigger.get("spec", {}))
        except Exception as e:
            logger.warning(
                f"Skipping invalid TriggeredHealthCheck {namespace}/{trigger_name}: {e}"
            )
            continue

        if not spec.enabled:
            continue
        if not trigger_executor.selector_matches(
            spec.deploymentRollout.selector.matchLabels, deployment_labels
        ):
            continue
        if trigger_executor.is_in_cooldown(
            trigger.get("status", {}), name, spec.cooldownSeconds
        ):
            logger.info(
                f"TriggeredHealthCheck {namespace}/{trigger_name} skipped for "
                f"{namespace}/{name}: within cooldown window"
            )
            continue

        if spec.delaySeconds > 0:
            fire_at = trigger_executor.compute_fire_at(spec.delaySeconds)
            await trigger_executor.add_pending(
                api=context.k8s_api,
                trigger_name=trigger_name,
                namespace=namespace,
                deployment=name,
                fire_at=fire_at,
                old_image=old_image,
                new_image=new_image,
            )
            logger.info(
                f"Rollout of {namespace}/{name} matched TriggeredHealthCheck "
                f"{namespace}/{trigger_name}; check scheduled for {fire_at} "
                f"(delay {spec.delaySeconds}s)"
            )
            continue

        logger.info(
            f"Rollout of {namespace}/{name} matched TriggeredHealthCheck "
            f"{namespace}/{trigger_name}"
        )
        task = asyncio.create_task(
            trigger_executor.spawn_check(
                trigger_name=trigger_name,
                namespace=namespace,
                trigger_uid=trigger_uid,
                spec=spec,
                deployment=name,
                old_image=old_image,
                new_image=new_image,
                k8s_api=context.k8s_api,
            )
        )
        trigger_executor.track_task(task)


@kopf.on.timer(GROUP, VERSION, "triggeredhealthchecks", interval=15.0)  # type: ignore[arg-type]
async def on_triggeredhealthcheck_timer(
    *,
    spec: Dict[str, Any],
    status: Dict[str, Any],
    name: str,
    namespace: str,
    uid: str,
    logger: kopf.Logger,
    **kwargs: Any,
) -> None:
    """Fire delayed checks whose scheduled time has arrived.

    Pending entries are claimed (removed from status) before spawning so a check is
    never run twice, even across operator restarts.
    """
    pending = (status or {}).get("pending", [])
    if not pending:
        return

    due = trigger_executor.due_pending(pending)
    if not due:
        return

    try:
        parsed = TriggeredHealthCheckSpec(**spec)
    except Exception as e:
        logger.warning(f"Skipping timer for invalid {namespace}/{name}: {e}")
        return

    # Claim the due entries first so a slow spawn can't be double-processed.
    await trigger_executor.remove_pending(context.k8s_api, name, namespace, due)

    for entry in due:
        logger.info(
            f"Delayed check for {namespace}/{entry.get('deployment')} is due; "
            f"running TriggeredHealthCheck {namespace}/{name}"
        )
        task = asyncio.create_task(
            trigger_executor.spawn_check(
                trigger_name=name,
                namespace=namespace,
                trigger_uid=uid,
                spec=parsed,
                deployment=entry.get("deployment"),
                old_image=entry.get("oldImage") or "",
                new_image=entry.get("newImage") or "",
                k8s_api=context.k8s_api,
            )
        )
        trigger_executor.track_task(task)


async def set_triggeredhealthcheck_condition(
    name: str,
    namespace: str,
    condition_type: TriggeredHealthCheckConditionType,
    status: ConditionStatus,
    reason: str,
    message: str,
) -> None:
    """Add or update a condition on a TriggeredHealthCheck resource."""
    condition = HealthCheckCondition(
        type=condition_type,
        status=status,
        lastTransitionTime=get_current_time_iso(),
        reason=reason,
        message=message,
    )

    try:
        resource = await asyncio.to_thread(
            context.k8s_api.get_namespaced_custom_object,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural="triggeredhealthchecks",
            name=name,
        )

        conditions = resource.get("status", {}).get("conditions", [])
        condition_dict = {
            "type": condition.type,
            "status": condition.status.value,
            "lastTransitionTime": condition.lastTransitionTime,
            "reason": condition.reason,
            "message": condition.message,
        }

        existing_idx = next(
            (i for i, c in enumerate(conditions) if c.get("type") == condition.type),
            None,
        )
        if existing_idx is not None:
            conditions[existing_idx] = condition_dict
        else:
            conditions.append(condition_dict)

        await asyncio.to_thread(
            context.k8s_api.patch_namespaced_custom_object_status,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural="triggeredhealthchecks",
            name=name,
            body={"status": {"conditions": conditions}},
        )
    except Exception as e:
        logger.error(f"Failed to set condition on {namespace}/{name}: {e}")
