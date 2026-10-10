"""Deletion of finished HealthCheck resources once they outlive their TTL.

Backs the ``cleanupCompletedChecks`` / ``completedCheckTTLHours`` operator
settings. Every HealthCheck stays in the cluster after it finishes, so without
this pass they accumulate forever. A ScheduledHealthCheck keeps its own summary
of each run in ``status.history``, which is not affected by deleting the
HealthCheck objects themselves.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from kubernetes import client

from holmes_operator.models import CheckPhase

logger = logging.getLogger(__name__)

GROUP = "holmesgpt.dev"
VERSION = "v1alpha1"
PLURAL = "healthchecks"

# Phases after which a HealthCheck never changes again. Pending and Running
# checks are never deleted.
TERMINAL_PHASES = frozenset({CheckPhase.COMPLETED.value, CheckPhase.FAILED.value})

# How many HealthChecks to fetch per list call, so a cluster where thousands
# have already piled up is not loaded in a single response.
LIST_PAGE_SIZE = 500


def parse_completion_time(value: Any) -> Optional[datetime]:
    """Parse ``status.completionTime`` into an aware datetime.

    Returns None when the value is missing or not an ISO 8601 timestamp.
    Timestamps without an offset are treated as UTC.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        # datetime.fromisoformat only accepts a trailing "Z" from Python 3.11.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_expired(resource: Dict[str, Any], now: datetime, ttl: timedelta) -> bool:
    """Whether a HealthCheck is finished and finished at least ``ttl`` ago.

    A finished check whose completion time is missing or malformed is kept:
    its age is unknown, and deleting it could discard a result too early.
    """
    status = resource.get("status") or {}
    if status.get("phase") not in TERMINAL_PHASES:
        return False

    completed_at = parse_completion_time(status.get("completionTime"))
    if completed_at is None:
        metadata = resource.get("metadata") or {}
        logger.warning(
            f"Keeping HealthCheck {metadata.get('namespace')}/{metadata.get('name')}: "
            f"phase is {status.get('phase')} but completionTime "
            f"{status.get('completionTime')!r} is not a valid timestamp"
        )
        return False

    return now - completed_at >= ttl


async def cleanup_completed_checks(
    k8s_api: client.CustomObjectsApi,
    ttl_hours: int,
    now: Optional[datetime] = None,
) -> int:
    """Delete every Completed or Failed HealthCheck older than ``ttl_hours``.

    Errors are logged rather than raised, so a failed pass is simply retried on
    the next run. Returns the number of HealthChecks deleted.
    """
    now = now or datetime.now(timezone.utc)
    ttl = timedelta(hours=ttl_hours)

    scanned = 0
    deleted = 0
    continue_token: Optional[str] = None

    while True:
        list_kwargs: Dict[str, Any] = {"limit": LIST_PAGE_SIZE}
        if continue_token:
            list_kwargs["_continue"] = continue_token
        try:
            page = await asyncio.to_thread(
                k8s_api.list_cluster_custom_object,
                group=GROUP,
                version=VERSION,
                plural=PLURAL,
                **list_kwargs,
            )
        except Exception as e:
            logger.error(f"Failed to list HealthChecks for cleanup: {e}")
            break

        for resource in page.get("items", []):
            scanned += 1
            if is_expired(resource, now, ttl) and await _delete_healthcheck(
                k8s_api, resource
            ):
                deleted += 1

        continue_token = (page.get("metadata") or {}).get("continue")
        if not continue_token:
            break

    if deleted:
        logger.info(
            f"Deleted {deleted} of {scanned} HealthChecks that finished more than "
            f"{ttl_hours}h ago"
        )
    else:
        logger.debug(
            f"No HealthChecks finished more than {ttl_hours}h ago "
            f"({scanned} scanned)"
        )
    return deleted


async def _delete_healthcheck(
    k8s_api: client.CustomObjectsApi, resource: Dict[str, Any]
) -> bool:
    """Delete one HealthCheck. Returns True if this call deleted it."""
    metadata = resource.get("metadata") or {}
    name = metadata.get("name")
    namespace = metadata.get("namespace")
    uid = metadata.get("uid")
    if not name or not namespace:
        return False

    try:
        await asyncio.to_thread(
            k8s_api.delete_namespaced_custom_object,
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=name,
            # Only delete the object that was listed, never a newer one that
            # reused its name in the meantime.
            body={"preconditions": {"uid": uid}} if uid else None,
        )
    except client.exceptions.ApiException as e:
        if e.status in (404, 409):
            # Already gone, or replaced by a new object with the same name.
            return False
        logger.warning(f"Failed to delete HealthCheck {namespace}/{name}: {e}")
        return False
    except Exception as e:
        logger.warning(f"Failed to delete HealthCheck {namespace}/{name}: {e}")
        return False

    logger.debug(f"Deleted finished HealthCheck {namespace}/{name}")
    return True
