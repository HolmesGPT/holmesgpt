"""Kopf handlers for HealthCheck CRD."""

import asyncio
import logging
from typing import Any, Dict, Optional

import kopf

from holmes_operator import context
from holmes_operator.models import (
    CheckResponse,
    CheckStatus,
    ConditionStatus,
    HealthCheckCondition,
    HealthCheckSpec,
)
from holmes_operator.utils import (
    add_healthcheck_condition,
    get_current_time_iso,
    set_healthcheck_completed,
    set_healthcheck_failed,
    set_healthcheck_pending,
    set_healthcheck_running,
)

logger = logging.getLogger(__name__)


async def _execute_healthcheck(
    spec: Dict[str, Any],
    name: str,
    namespace: str,
    uid: str,
    generation: Optional[int],
    logger: kopf.Logger,
    body: Optional[Any] = None,
) -> None:
    """
    Execute a HealthCheck: validate spec, call Holmes API, update status.

    Shared logic used by both create and update handlers.

    Args:
        spec: The HealthCheck spec dict
        name: Resource name
        namespace: Resource namespace
        uid: Resource UID
        generation: metadata.generation to store as observedGeneration
        logger: Kopf logger
        body: Full resource body (for kopf.event)
    """
    logger.info(f"Executing HealthCheck: {namespace}/{name} (generation={generation})")

    # Set status to Pending
    await set_healthcheck_pending(
        api=context.k8s_api,
        name=name,
        namespace=namespace,
    )

    try:
        # Parse and validate spec using Pydantic
        check_spec = HealthCheckSpec(**spec)

        # Update status to Running
        await set_healthcheck_running(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
        )

        logger.info(
            f"Executing check {namespace}/{name} via Holmes API",
            extra={
                "check_name": name,
                "namespace": namespace,
                "query": check_spec.query[:100],
                "mode": check_spec.mode.value,
            },
        )

        # Call Holmes API
        result: CheckResponse = await context.api_client.execute_check(
            check_name=f"{namespace}/{name}",
            query=check_spec.query,
            timeout=check_spec.timeout,
            mode=check_spec.mode.value,
            destinations=[d.model_dump() for d in check_spec.destinations],
            model=check_spec.model,
        )

        # Use notifications directly from result (already NotificationStatus instances)
        notifications = result.notifications or []

        # Update status to Completed, setting observedGeneration
        await set_healthcheck_completed(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            result=result.status,
            message=result.message,
            rationale=result.rationale,
            duration=result.duration,
            error=result.error,
            model_used=result.model_used,
            notifications=notifications if notifications else None,
            observed_generation=generation,
        )

        # Add condition based on result
        if result.status == CheckStatus.PASS:
            await add_healthcheck_condition(
                api=context.k8s_api,
                name=name,
                namespace=namespace,
                condition=HealthCheckCondition(
                    type="Complete",
                    status=ConditionStatus.TRUE,
                    lastTransitionTime=get_current_time_iso(),
                    reason="CheckPassed",
                    message="Health check passed successfully",
                ),
            )
        elif result.status == CheckStatus.FAIL:
            await add_healthcheck_condition(
                api=context.k8s_api,
                name=name,
                namespace=namespace,
                condition=HealthCheckCondition(
                    type="Complete",
                    status=ConditionStatus.TRUE,
                    lastTransitionTime=get_current_time_iso(),
                    reason="CheckFailed",
                    message=f"Health check failed: {result.message}",
                ),
            )
        else:  # error
            await add_healthcheck_condition(
                api=context.k8s_api,
                name=name,
                namespace=namespace,
                condition=HealthCheckCondition(
                    type="Failed",
                    status=ConditionStatus.TRUE,
                    lastTransitionTime=get_current_time_iso(),
                    reason="ExecutionError",
                    message=f"Check execution error: {result.error or result.message}",
                ),
            )

        logger.info(
            f"HealthCheck {namespace}/{name} completed with status: {result.status}",
            extra={
                "check_name": name,
                "namespace": namespace,
                "status": result.status,
                "duration": result.duration,
            },
        )

        # Create Kubernetes event
        kopf.event(
            objs=body,
            type="Normal" if result.status == CheckStatus.PASS else "Warning",
            reason=f"Check{result.status.capitalize()}",
            message=f"Health check {result.status}: {result.message}",
        )

    except Exception as e:
        logger.error(
            f"Failed to execute HealthCheck {namespace}/{name}: {e}",
            exc_info=True,
            extra={"check_name": name, "namespace": namespace, "error": str(e)},
        )

        # Update status to Failed, still setting observedGeneration so we don't retry forever
        await set_healthcheck_failed(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            message=f"Operator error: {str(e)}",
            error=str(e),
            observed_generation=generation,
        )

        # Add failed condition
        await add_healthcheck_condition(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            condition=HealthCheckCondition(
                type="Failed",
                status=ConditionStatus.TRUE,
                lastTransitionTime=get_current_time_iso(),
                reason="OperatorError",
                message=f"Operator failed to execute check: {str(e)}",
            ),
        )

        # Create error event
        kopf.event(
            objs=body,
            type="Warning",
            reason="OperatorError",
            message=f"Failed to execute health check: {str(e)}",
        )

        # Re-raise to let kopf handle retry if needed
        raise


@kopf.on.create("holmesgpt.dev", "v1alpha1", "healthchecks")
async def on_healthcheck_create(
    spec: Dict[str, Any],
    name: str,
    namespace: str,
    uid: str,
    logger: kopf.Logger,
    **kwargs: Any,
) -> None:
    """
    Handle HealthCheck creation.

    Flow:
    1. Update status to "Pending"
    2. Validate spec fields
    3. Update status to "Running"
    4. Call Holmes API via HTTP client
    5. Update status with result ("Completed" or "Failed")
    6. Set conditions and observedGeneration
    7. Clear rerun annotation if present (so next apply can re-add it)
    """
    body = kwargs.get("body", {})
    generation = body.get("metadata", {}).get("generation")

    await _execute_healthcheck(
        spec=spec,
        name=name,
        namespace=namespace,
        uid=uid,
        generation=generation,
        logger=logger,
        body=body,
    )

    # Clear rerun annotation if present, so that the next kubectl apply
    # (which restores it from the manifest) triggers a re-run
    annotations = body.get("metadata", {}).get("annotations", {})
    if annotations.get("holmesgpt.dev/rerun") == "true":
        await _clear_rerun_annotation(name=name, namespace=namespace, logger=logger)


@kopf.on.update("holmesgpt.dev", "v1alpha1", "healthchecks")
async def on_healthcheck_update(
    old: Dict[str, Any],
    new: Dict[str, Any],
    name: str,
    namespace: str,
    logger: kopf.Logger,
    **kwargs,
):
    """
    Handle HealthCheck updates.

    Re-execution triggers (checked in order):
    1. Generation-based: if metadata.generation != status.observedGeneration,
       the spec has changed since last execution → re-run.
    2. Annotation-based: if "holmesgpt.dev/rerun=true" annotation is newly added,
       re-run even without spec changes.
    """
    body = new
    metadata = body.get("metadata", {})
    status = body.get("status", {})
    generation = metadata.get("generation")
    observed_generation = status.get("observedGeneration")

    # Trigger 1: Generation changed (spec was modified via kubectl apply)
    if generation is not None and generation != observed_generation:
        logger.info(
            f"Re-running HealthCheck {namespace}/{name}: "
            f"generation={generation} != observedGeneration={observed_generation}"
        )
        await _execute_healthcheck(
            spec=new.get("spec", {}),
            name=name,
            namespace=namespace,
            uid=metadata.get("uid", ""),
            generation=generation,
            logger=logger,
            body=body,
        )
        return

    # Trigger 2: Rerun annotation (for re-running without spec changes)
    annotations = metadata.get("annotations", {})
    old_annotations = old.get("metadata", {}).get("annotations", {})
    if (
        annotations.get("holmesgpt.dev/rerun") == "true"
        and old_annotations.get("holmesgpt.dev/rerun") != "true"
    ):
        logger.info(f"Re-running HealthCheck via annotation: {namespace}/{name}")
        await _execute_healthcheck(
            spec=new.get("spec", {}),
            name=name,
            namespace=namespace,
            uid=metadata.get("uid", ""),
            generation=generation,
            logger=logger,
            body=body,
        )

        # Clear the rerun annotation so the user can set it again later
        await _clear_rerun_annotation(name=name, namespace=namespace, logger=logger)


async def _clear_rerun_annotation(
    name: str, namespace: str, logger: kopf.Logger
) -> None:
    """Remove the holmesgpt.dev/rerun annotation after processing."""
    try:
        await asyncio.to_thread(
            context.k8s_api.patch_namespaced_custom_object,
            group="holmesgpt.dev",
            version="v1alpha1",
            namespace=namespace,
            plural="healthchecks",
            name=name,
            body={
                "metadata": {
                    "annotations": {
                        "holmesgpt.dev/rerun": None,  # null removes the key
                    }
                }
            },
        )
        logger.info(f"Cleared rerun annotation on {namespace}/{name}")
    except Exception as e:
        logger.warning(
            f"Failed to clear rerun annotation on {namespace}/{name}: {e}"
        )
