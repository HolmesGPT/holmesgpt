"""Execution logic for TriggeredHealthCheck (deployment-rollout trigger).

Mirrors scheduler/job_executor.py: when a trigger fires, this spawns a HealthCheck
child (owned by the TriggeredHealthCheck) which goes through the normal HealthCheck
execution path, and records the trigger in the TriggeredHealthCheck status.
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from kubernetes import client

from holmes_operator import context
from holmes_operator.models import TriggeredHealthCheckSpec
from holmes_operator.utils import get_current_time_iso

logger = logging.getLogger(__name__)

GROUP = "holmesgpt.dev"
VERSION = "v1alpha1"

_active_tasks: set[asyncio.Task] = set()

# In-memory cache of the last seen pod-template per Deployment, keyed by
# "namespace/name" -> (template_hash, images). Used to detect rollouts from raw
# watch events without writing kopf diff annotations onto every Deployment in the
# cluster. Lost on operator restart by design: the first event for a Deployment
# after (re)start only establishes a baseline and is not treated as a rollout.
_last_template: Dict[str, Tuple[str, str]] = {}


def _log_task_exception(task: asyncio.Task) -> None:
    """Log any exception from a background task and drop it from the registry."""
    _active_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"Trigger background task raised an exception: {exc}", exc_info=exc)


def track_task(task: asyncio.Task) -> None:
    """Keep a strong reference to a background task until it completes."""
    _active_tasks.add(task)
    task.add_done_callback(_log_task_exception)


def extract_images(deployment_body: dict) -> str:
    """Return a stable, human-readable representation of a Deployment's images."""
    containers = (
        deployment_body.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    images = [c.get("image", "") for c in containers if c.get("image")]
    return ", ".join(images)


def _template_hash(template: dict) -> str:
    return hashlib.sha256(
        json.dumps(template, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def detect_rollout(key: str, deployment_body: dict) -> Optional[Tuple[str, str]]:
    """Detect whether a Deployment event represents a rollout (pod-template change).

    Updates the in-memory baseline cache and returns ``(old_images, new_images)``
    when the pod template changed relative to the last seen value, or ``None`` when
    this is a baseline observation or a non-template change (e.g. status/scale).
    """
    template = (deployment_body.get("spec") or {}).get("template")
    if template is None:
        return None

    new_hash = _template_hash(template)
    new_images = extract_images(deployment_body)

    previous = _last_template.get(key)
    _last_template[key] = (new_hash, new_images)

    if previous is None:
        return None  # baseline only

    prev_hash, prev_images = previous
    if prev_hash == new_hash:
        return None  # template unchanged

    return (prev_images, new_images)


def forget_deployment(key: str) -> None:
    """Drop a Deployment from the baseline cache (e.g. on deletion)."""
    _last_template.pop(key, None)


def clear_rollout_cache() -> None:
    """Reset the baseline cache (used in tests)."""
    _last_template.clear()


def selector_matches(match_labels: dict, labels: dict) -> bool:
    """Return True if ``labels`` contains all of ``match_labels``.

    An empty selector matches every Deployment in the namespace.
    """
    if not match_labels:
        return True
    return all(labels.get(k) == v for k, v in match_labels.items())


def render_query(
    template: str,
    deployment: str,
    namespace: str,
    old_image: str,
    new_image: str,
) -> str:
    """Substitute trigger context tokens into the query template.

    Supported tokens (whitespace-insensitive): ``{{ .deployment }}``,
    ``{{ .namespace }}``, ``{{ .old.image }}``, ``{{ .new.image }}``.
    """
    replacements = {
        r"\{\{\s*\.deployment\s*\}\}": deployment,
        r"\{\{\s*\.namespace\s*\}\}": namespace,
        r"\{\{\s*\.old\.image\s*\}\}": old_image or "unknown",
        r"\{\{\s*\.new\.image\s*\}\}": new_image or "unknown",
    }
    rendered = template
    for pattern, value in replacements.items():
        rendered = re.sub(pattern, lambda _m, v=value: v, rendered)
    return rendered


def compose_query(
    template: str,
    deployment: str,
    namespace: str,
    old_image: str,
    new_image: str,
) -> str:
    """Build the spawned check's query.

    The rollout facts are always prepended as a structured context header — so the
    model knows which Deployment/namespace and what changed even if the author's
    query is terse and uses none of the tokens — followed by the author's query with
    any tokens substituted.
    """
    header = (
        "This health check was triggered automatically by a Kubernetes Deployment "
        "rollout. Use this context when investigating:\n"
        f"- Deployment: {deployment}\n"
        f"- Namespace: {namespace}\n"
        f"- Previous image(s): {old_image or 'unknown'}\n"
        f"- New image(s): {new_image or 'unknown'}\n\n"
    )
    return header + render_query(template, deployment, namespace, old_image, new_image)


def is_in_cooldown(status: dict, deployment: str, cooldown_seconds: int) -> bool:
    """Return True if ``deployment`` fired within the cooldown window."""
    if cooldown_seconds <= 0:
        return False
    for entry in status.get("cooldowns", []):
        if entry.get("deployment") != deployment:
            continue
        raw = entry.get("lastTriggerTime")
        if not raw:
            return False
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() < cooldown_seconds
    return False


def compute_fire_at(delay_seconds: int) -> str:
    """ISO timestamp ``delay_seconds`` from now."""
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()


def due_pending(pending: List[dict]) -> List[dict]:
    """Return the pending entries whose fireAt is now or in the past."""
    now = datetime.now(timezone.utc)
    due = []
    for entry in pending:
        raw = entry.get("fireAt")
        if not raw:
            continue
        try:
            fire_at = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        if fire_at <= now:
            due.append(entry)
    return due


async def add_pending(
    api: client.CustomObjectsApi,
    trigger_name: str,
    namespace: str,
    deployment: str,
    fire_at: str,
    old_image: str,
    new_image: str,
) -> None:
    """Schedule a delayed check, debounced per Deployment (a newer rollout replaces
    any still-pending entry for the same Deployment)."""

    def modify_status(resource: dict) -> dict:
        status = resource.get("status", {})
        pending = [
            p for p in status.get("pending", []) if p.get("deployment") != deployment
        ]
        pending.append(
            {
                "deployment": deployment,
                "fireAt": fire_at,
                "scheduledAt": get_current_time_iso(),
                "oldImage": old_image,
                "newImage": new_image,
            }
        )
        return {"pending": pending}

    await _patch_status_with_retry(api, trigger_name, namespace, modify_status)


async def remove_pending(
    api: client.CustomObjectsApi,
    trigger_name: str,
    namespace: str,
    entries: List[dict],
) -> None:
    """Remove the given pending entries (matched by deployment + fireAt)."""
    keys = {(e.get("deployment"), e.get("fireAt")) for e in entries}

    def modify_status(resource: dict) -> dict:
        status = resource.get("status", {})
        pending = [
            p
            for p in status.get("pending", [])
            if (p.get("deployment"), p.get("fireAt")) not in keys
        ]
        return {"pending": pending}

    await _patch_status_with_retry(api, trigger_name, namespace, modify_status)


def generate_check_name(trigger_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{trigger_name}-{timestamp}-{uuid4().hex[:6]}"


def build_healthcheck_object(
    check_name: str,
    namespace: str,
    trigger_name: str,
    trigger_uid: str,
    spec: TriggeredHealthCheckSpec,
    deployment: str,
    old_image: str,
    new_image: str,
) -> dict:
    healthcheck = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "HealthCheck",
        "metadata": {
            "name": check_name,
            "namespace": namespace,
            "labels": {
                "holmesgpt.dev/triggered-by": trigger_name,
                "holmesgpt.dev/trigger-type": "deployment-rollout",
                "holmesgpt.dev/deployment": deployment,
            },
            "ownerReferences": [
                {
                    "apiVersion": f"{GROUP}/{VERSION}",
                    "kind": "TriggeredHealthCheck",
                    "name": trigger_name,
                    "uid": trigger_uid,
                    "controller": True,
                    "blockOwnerDeletion": True,
                }
            ],
        },
        "spec": {
            "query": compose_query(
                spec.query, deployment, namespace, old_image, new_image
            ),
            "timeout": spec.timeout,
            "mode": spec.mode.value,
        },
    }

    if spec.model:
        healthcheck["spec"]["model"] = spec.model
    if spec.destinations:
        healthcheck["spec"]["destinations"] = [
            d.model_dump() for d in spec.destinations
        ]
    return healthcheck


async def spawn_check(
    trigger_name: str,
    namespace: str,
    trigger_uid: str,
    spec: TriggeredHealthCheckSpec,
    deployment: str,
    old_image: str,
    new_image: str,
    k8s_api: client.CustomObjectsApi,
) -> None:
    """Create a HealthCheck for this rollout and record it in the trigger status."""
    try:
        check_name = generate_check_name(trigger_name)
        logger.info(
            f"TriggeredHealthCheck {namespace}/{trigger_name} fired by rollout of "
            f"{namespace}/{deployment}; creating HealthCheck {check_name}",
            extra={
                "trigger_name": trigger_name,
                "namespace": namespace,
                "deployment": deployment,
                "check_name": check_name,
            },
        )

        healthcheck = build_healthcheck_object(
            check_name,
            namespace,
            trigger_name,
            trigger_uid,
            spec,
            deployment,
            old_image,
            new_image,
        )

        await asyncio.to_thread(
            k8s_api.create_namespaced_custom_object,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural="healthchecks",
            body=healthcheck,
        )

        await record_trigger(
            api=k8s_api,
            trigger_name=trigger_name,
            namespace=namespace,
            deployment=deployment,
            check_name=check_name,
            old_image=old_image,
            new_image=new_image,
        )

    except Exception as e:
        logger.error(
            f"Failed to execute TriggeredHealthCheck {namespace}/{trigger_name}: {e}",
            exc_info=True,
        )


async def record_trigger(
    api: client.CustomObjectsApi,
    trigger_name: str,
    namespace: str,
    deployment: str,
    check_name: str,
    old_image: str,
    new_image: str,
) -> None:
    """Update TriggeredHealthCheck status with this trigger (history + cooldown)."""

    def modify_status(resource: dict) -> dict:
        status = resource.get("status", {})
        history = status.get("history", [])
        cooldowns = status.get("cooldowns", [])
        now_iso = get_current_time_iso()

        # Upsert the cooldown entry for this deployment
        cooldowns = [c for c in cooldowns if c.get("deployment") != deployment]
        cooldowns.append({"deployment": deployment, "lastTriggerTime": now_iso})

        history.insert(
            0,
            {
                "triggerTime": now_iso,
                "deployment": deployment,
                "checkName": check_name,
                "oldImage": old_image,
                "newImage": new_image,
            },
        )
        max_history = context.config.max_history_items if context.config else 10
        history = history[:max_history]

        return {
            "lastTriggerTime": now_iso,
            "lastTriggerDeployment": deployment,
            "triggerCount": (status.get("triggerCount") or 0) + 1,
            "cooldowns": cooldowns,
            "history": history,
        }

    await _patch_status_with_retry(api, trigger_name, namespace, modify_status)


async def _patch_status_with_retry(
    api: client.CustomObjectsApi,
    trigger_name: str,
    namespace: str,
    modify_fn,
    max_retries: int = 5,
) -> None:
    """Read-modify-write TriggeredHealthCheck status with conflict retry."""
    for attempt in range(max_retries):
        try:
            resource = await asyncio.to_thread(
                api.get_namespaced_custom_object,
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural="triggeredhealthchecks",
                name=trigger_name,
            )

            status_updates = modify_fn(resource)
            resource_version = resource.get("metadata", {}).get("resourceVersion")

            await asyncio.to_thread(
                api.patch_namespaced_custom_object_status,
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural="triggeredhealthchecks",
                name=trigger_name,
                body={
                    "metadata": {"resourceVersion": resource_version},
                    "status": status_updates,
                },
            )
            return

        except client.exceptions.ApiException as e:
            if e.status == 409:
                logger.debug(
                    f"Conflict updating {namespace}/{trigger_name} status "
                    f"(attempt {attempt + 1}/{max_retries}), retrying..."
                )
                if attempt == max_retries - 1:
                    raise Exception(
                        f"Max retries ({max_retries}) exceeded for status update"
                    ) from e
                await asyncio.sleep(0.1 * (attempt + 1))
            else:
                raise
