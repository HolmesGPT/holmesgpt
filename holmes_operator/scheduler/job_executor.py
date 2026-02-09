"""
Job executor for scheduled health checks.

Handles execution of scheduled checks by creating HealthCheck resources
and watching for their completion.
"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from kubernetes import client

from holmes_operator.models import CheckResult, ScheduledHealthCheckSpec
from holmes_operator.utils import get_current_time_iso

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task):
    """
    Log any exception that occurred in a background task.

    Args:
        task: The completed asyncio Task to check for exceptions
    """
    if task.cancelled():
        return

    exc = task.exception()
    if exc is not None:
        logger.error(
            f"Background task raised an exception: {exc}",
            exc_info=exc,
        )


async def execute_scheduled_check(
    name: str,
    namespace: str,
    spec: ScheduledHealthCheckSpec,
    scheduled_uid: str,
    k8s_api: client.CustomObjectsApi,
):
    """
    Execute a scheduled health check by creating a HealthCheck resource.

    Args:
        name: ScheduledHealthCheck name
        namespace: ScheduledHealthCheck namespace
        spec: ScheduledHealthCheck spec
        scheduled_uid: UID of the ScheduledHealthCheck resource (for ownerReferences)
        k8s_api: Kubernetes API client

    This function:
    1. Generates a unique HealthCheck name
    2. Creates a HealthCheck resource with appropriate labels and ownerReferences
    3. Updates the parent ScheduledHealthCheck status (adds to active[])
    4. Starts an async watcher for completion
    """
    try:
        # 1. Generate unique name with timestamp and random suffix to avoid collisions
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        random_suffix = uuid4().hex[:6]
        check_name = f"{name}-{timestamp}-{random_suffix}"

        logger.info(
            f"Executing scheduled check {namespace}/{name}, creating HealthCheck: {check_name}",
            extra={
                "scheduled_name": name,
                "namespace": namespace,
                "check_name": check_name,
                "schedule": spec.schedule,
            },
        )

        # 2. Create HealthCheck resource
        healthcheck = {
            "apiVersion": "holmesgpt.dev/v1alpha1",
            "kind": "HealthCheck",
            "metadata": {
                "name": check_name,
                "namespace": namespace,
                "labels": {
                    "holmesgpt.dev/scheduled-by": name,
                    "holmesgpt.dev/schedule-type": "scheduled",
                },
                "ownerReferences": [
                    {
                        "apiVersion": "holmesgpt.dev/v1alpha1",
                        "kind": "ScheduledHealthCheck",
                        "name": name,
                        "uid": scheduled_uid,
                        "blockOwnerDeletion": True,
                    }
                ],
            },
            "spec": {
                "query": spec.query,
                "timeout": spec.timeout,
                "mode": spec.mode.value,
            },
        }

        # Add optional fields
        if spec.model:
            healthcheck["spec"]["model"] = spec.model

        if spec.destinations:
            healthcheck["spec"]["destinations"] = [
                d.model_dump() for d in spec.destinations
            ]

        # 3. Create HealthCheck via Kubernetes API
        await asyncio.to_thread(
            k8s_api.create_namespaced_custom_object,
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="healthchecks",
            body=healthcheck,
        )

        # 4. Update ScheduledHealthCheck status - add to active[]
        await _add_active_check(
            api=k8s_api,
            scheduled_name=name,
            scheduled_namespace=namespace,
            check_name=check_name,
            check_namespace=namespace,
            start_time=get_current_time_iso(),
        )

        # 5. Start async watcher for completion
        task = asyncio.create_task(
            watch_healthcheck_completion(
                scheduled_name=name,
                scheduled_namespace=namespace,
                check_name=check_name,
                check_namespace=namespace,
                k8s_api=k8s_api,
            )
        )
        task.add_done_callback(_log_task_exception)

        logger.info(f"Successfully created HealthCheck {namespace}/{check_name}")

    except Exception as e:
        logger.error(
            f"Failed to execute scheduled check {namespace}/{name}: {e}",
            exc_info=True,
            extra={"scheduled_name": name, "namespace": namespace, "error": str(e)},
        )
        # TODO: Update ScheduledHealthCheck condition with error


async def watch_healthcheck_completion(
    scheduled_name: str,
    scheduled_namespace: str,
    check_name: str,
    check_namespace: str,
    k8s_api: client.CustomObjectsApi,
    max_wait_seconds: int = 600,
):
    """
    Watch a HealthCheck until it completes, then update parent status.

    Args:
        scheduled_name: ScheduledHealthCheck name
        scheduled_namespace: ScheduledHealthCheck namespace
        check_name: HealthCheck name to watch
        check_namespace: HealthCheck namespace
        k8s_api: Kubernetes API client
        max_wait_seconds: Maximum time to wait for completion (default 10 minutes)

    Polls the HealthCheck resource with exponential backoff until phase is
    Completed or Failed, then moves it from active[] to history[] in the
    parent ScheduledHealthCheck status.
    """
    start_time = datetime.now(timezone.utc)
    poll_interval = 5  # Start with 5 seconds
    max_poll_interval = 30  # Max 30 seconds

    logger.info(
        f"Started watching HealthCheck {check_namespace}/{check_name}",
        extra={
            "scheduled_name": scheduled_name,
            "scheduled_namespace": scheduled_namespace,
            "check_name": check_name,
        },
    )

    while True:
        # Check timeout
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        if elapsed > max_wait_seconds:
            logger.error(
                f"Timeout watching HealthCheck {check_namespace}/{check_name} "
                f"after {elapsed:.1f} seconds",
                extra={
                    "scheduled_name": scheduled_name,
                    "scheduled_namespace": scheduled_namespace,
                    "check_name": check_name,
                },
            )
            # Remove from active[] and record timeout in history[]
            await _move_to_history(
                api=k8s_api,
                scheduled_name=scheduled_name,
                scheduled_namespace=scheduled_namespace,
                check_name=check_name,
                result=CheckResult.ERROR,
                message=f"Watcher timeout after {elapsed:.1f} seconds",
                duration=elapsed,
                execution_time=start_time.isoformat(),
            )
            break

        try:
            # Get HealthCheck status
            hc = await asyncio.to_thread(
                k8s_api.get_namespaced_custom_object,
                group="holmesgpt.dev",
                version="v1alpha1",
                namespace=check_namespace,
                plural="healthchecks",
                name=check_name,
            )

            status = hc.get("status", {})
            phase = status.get("phase")

            logger.debug(
                f"HealthCheck {check_namespace}/{check_name} phase: {phase}",
                extra={
                    "scheduled_name": scheduled_name,
                    "check_name": check_name,
                    "phase": phase,
                },
            )

            # Check if completed
            if phase in ["Completed", "Failed"]:
                # Extract result details
                result_str = status.get("result", "error")
                message = status.get("message", "")
                duration = status.get("duration", 0.0)

                logger.info(
                    f"HealthCheck {check_namespace}/{check_name} completed with result: {result_str}",
                    extra={
                        "scheduled_name": scheduled_name,
                        "check_name": check_name,
                        "result": result_str,
                        "duration": duration,
                    },
                )

                # Update parent ScheduledHealthCheck - move to history
                try:
                    result = (
                        CheckResult(result_str)
                        if result_str in ["pass", "fail", "error"]
                        else CheckResult.ERROR
                    )
                except ValueError:
                    result = CheckResult.ERROR

                await _move_to_history(
                    api=k8s_api,
                    scheduled_name=scheduled_name,
                    scheduled_namespace=scheduled_namespace,
                    check_name=check_name,
                    result=result,
                    message=message,
                    duration=duration,
                    execution_time=start_time.isoformat(),
                )

                break

        except client.exceptions.ApiException as e:
            if e.status == 404:
                # HealthCheck was deleted
                logger.warning(
                    f"HealthCheck {check_namespace}/{check_name} was deleted",
                    extra={"scheduled_name": scheduled_name, "check_name": check_name},
                )
                # Remove from active[] without adding to history
                await _remove_from_active(
                    api=k8s_api,
                    scheduled_name=scheduled_name,
                    scheduled_namespace=scheduled_namespace,
                    check_name=check_name,
                )
                break
            else:
                logger.error(
                    f"API error watching HealthCheck {check_namespace}/{check_name}: {e}",
                    exc_info=True,
                )

        except Exception as e:
            logger.error(
                f"Error watching HealthCheck {check_namespace}/{check_name}: {e}",
                exc_info=True,
            )

        # Exponential backoff (max 30s)
        await asyncio.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, max_poll_interval)


# Internal helper functions (will be moved to utils.py in Step 5)


async def _patch_status_with_retry(
    api: client.CustomObjectsApi,
    scheduled_name: str,
    scheduled_namespace: str,
    modify_fn,
    max_retries: int = 5,
):
    """
    Safely patch ScheduledHealthCheck status with conflict retry.

    Args:
        api: Kubernetes API client
        scheduled_name: ScheduledHealthCheck name
        scheduled_namespace: ScheduledHealthCheck namespace
        modify_fn: Callable that takes the resource dict and returns status_updates dict
        max_retries: Maximum number of retry attempts on conflict

    Raises:
        Exception: If max retries exceeded or other error occurs
    """
    for attempt in range(max_retries):
        try:
            # Get current resource with resourceVersion
            resource = await asyncio.to_thread(
                api.get_namespaced_custom_object,
                group="holmesgpt.dev",
                version="v1alpha1",
                namespace=scheduled_namespace,
                plural="scheduledhealthchecks",
                name=scheduled_name,
            )

            # Let caller compute status updates
            status_updates = modify_fn(resource)

            # Include resourceVersion for conflict detection
            resource_version = resource.get("metadata", {}).get("resourceVersion")
            body = {
                "metadata": {"resourceVersion": resource_version},
                "status": status_updates,
            }

            # Patch status
            await asyncio.to_thread(
                api.patch_namespaced_custom_object_status,
                group="holmesgpt.dev",
                version="v1alpha1",
                namespace=scheduled_namespace,
                plural="scheduledhealthchecks",
                name=scheduled_name,
                body=body,
            )

            # Success
            return

        except client.exceptions.ApiException as e:
            if e.status == 409:
                # Conflict - retry
                logger.debug(
                    f"Conflict updating {scheduled_namespace}/{scheduled_name} "
                    f"status (attempt {attempt + 1}/{max_retries}), retrying..."
                )
                if attempt == max_retries - 1:
                    raise Exception(
                        f"Max retries ({max_retries}) exceeded for status update"
                    ) from e
                # Small delay before retry
                await asyncio.sleep(0.1 * (attempt + 1))
            else:
                raise


async def _add_active_check(
    api: client.CustomObjectsApi,
    scheduled_name: str,
    scheduled_namespace: str,
    check_name: str,
    check_namespace: str,
    start_time: str,
):
    """Add HealthCheck to active[] list in ScheduledHealthCheck status."""
    try:
        # Get HealthCheck UID (outside retry loop)
        hc = await asyncio.to_thread(
            api.get_namespaced_custom_object,
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=check_namespace,
            plural="healthchecks",
            name=check_name,
        )
        check_uid = hc.get("metadata", {}).get("uid", "")

        # Define status modification function
        def modify_status(resource):
            status = resource.get("status", {})
            active = status.get("active", [])

            # Append to active list
            active.append(
                {
                    "name": check_name,
                    "namespace": check_namespace,
                    "uid": check_uid,
                    "startTime": start_time,
                }
            )

            return {"active": active, "lastScheduleTime": start_time}

        # Update status with retry on conflict
        await _patch_status_with_retry(
            api=api,
            scheduled_name=scheduled_name,
            scheduled_namespace=scheduled_namespace,
            modify_fn=modify_status,
        )

    except Exception as e:
        logger.error(f"Failed to add active check: {e}", exc_info=True)


async def _move_to_history(
    api: client.CustomObjectsApi,
    scheduled_name: str,
    scheduled_namespace: str,
    check_name: str,
    result: CheckResult,
    message: str,
    duration: float,
    execution_time: str,
):
    """Move HealthCheck from active[] to history[] in ScheduledHealthCheck status."""
    try:
        # Define status modification function
        def modify_status(resource):
            status = resource.get("status", {})
            active = status.get("active", [])
            history = status.get("history", [])

            # Remove from active
            active = [ref for ref in active if ref.get("name") != check_name]

            # Prepend to history (newest first)
            history.insert(
                0,
                {
                    "executionTime": execution_time,
                    "result": result.value,
                    "duration": duration,
                    "checkName": check_name,
                    "message": message,
                },
            )

            # Trim history to MAX_HISTORY_ITEMS (default 10)
            # TODO: Get from context.config.max_history_items
            max_history = 10
            history = history[:max_history]

            # Build status updates
            status_updates = {
                "active": active,
                "history": history,
                "lastResult": result.value,
                "message": message,
            }

            # Update lastSuccessfulTime if passed
            if result == CheckResult.PASS:
                status_updates["lastSuccessfulTime"] = get_current_time_iso()

            return status_updates

        # Update status with retry on conflict
        await _patch_status_with_retry(
            api=api,
            scheduled_name=scheduled_name,
            scheduled_namespace=scheduled_namespace,
            modify_fn=modify_status,
        )

    except Exception as e:
        logger.error(f"Failed to move to history: {e}", exc_info=True)


async def _remove_from_active(
    api: client.CustomObjectsApi,
    scheduled_name: str,
    scheduled_namespace: str,
    check_name: str,
):
    """Remove HealthCheck from active[] list without adding to history."""
    try:
        # Define status modification function
        def modify_status(resource):
            status = resource.get("status", {})
            active = status.get("active", [])

            # Remove from active
            active = [ref for ref in active if ref.get("name") != check_name]

            return {"active": active}

        # Update status with retry on conflict
        await _patch_status_with_retry(
            api=api,
            scheduled_name=scheduled_name,
            scheduled_namespace=scheduled_namespace,
            modify_fn=modify_status,
        )

    except Exception as e:
        logger.error(f"Failed to remove from active: {e}", exc_info=True)
