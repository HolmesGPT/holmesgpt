"""Kopf handlers for HealthCheck CRD."""

import logging
from typing import Any, Dict

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
    6. Set conditions
    """
    logger.info(f"Creating HealthCheck: {namespace}/{name}")

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

        # Update status to Completed
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
            objs=kwargs.get("body"),
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

        # Update status to Failed
        await set_healthcheck_failed(
            api=context.k8s_api,
            name=name,
            namespace=namespace,
            message=f"Operator error: {str(e)}",
            error=str(e),
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
            objs=kwargs.get("body"),
            type="Warning",
            reason="OperatorError",
            message=f"Failed to execute health check: {str(e)}",
        )

        # Re-raise to let kopf handle retry if needed
        raise


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

    Support for re-execution via annotation:
    - Check for "holmesgpt.dev/rerun=true" annotation
    - Reset status and re-execute
    """
    # Check for rerun annotation
    annotations = new.get("metadata", {}).get("annotations", {})
    old_annotations = old.get("metadata", {}).get("annotations", {})
    if (
        annotations.get("holmesgpt.dev/rerun") == "true"
        and old_annotations.get("holmesgpt.dev/rerun") != "true"
    ):
        logger.info(f"Re-running HealthCheck: {namespace}/{name}")

        # Trigger re-execution by calling create handler
        await on_healthcheck_create(
            spec=new.get("spec", {}),
            name=name,
            namespace=namespace,
            uid=new.get("metadata", {}).get("uid", ""),
            logger=logger,
            **kwargs,
        )
