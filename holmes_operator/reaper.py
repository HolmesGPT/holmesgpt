"""Reaper for completed HealthCheck resources.

Implements the ``operator.cleanupCompletedChecks`` / ``operator.completedCheckTTLHours``
settings. When enabled, a background task periodically lists all HealthCheck CRs
(cluster-wide, matching the operator's cluster-wide deployment) and deletes those
in a terminal phase (``Completed`` or ``Failed``) whose ``status.completionTime``
is older than the configured TTL.

Semantics:
- Only terminal phases are eligible; ``Pending`` and ``Running`` checks are never
  touched.
- ``Failed`` checks are reaped too: operator errors are terminal and also carry a
  ``completionTime``, so leaving them would silently accumulate failed CRs.
- A terminal check with a missing or unparsable ``completionTime`` is kept (and
  logged) rather than deleted, so a malformed timestamp never causes data loss.
- Deletion is idempotent: a 404 (e.g. the CR was deleted concurrently) is
  tolerated.

Note: per-ScheduledHealthCheck history trimming (``maxHistoryItems``) is a separate
mechanism handled by the scheduler's job executor and is unaffected by this reaper.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from holmes_operator.config import OperatorConfig
from holmes_operator.models import CheckPhase

logger = logging.getLogger(__name__)

GROUP = "holmesgpt.dev"
VERSION = "v1alpha1"
PLURAL = "healthchecks"

# How often the reaper scans for expired HealthChecks. One hour keeps the
# effective TTL within [ttl, ttl + 1h], which is fine for hour-granularity TTLs.
REAPER_INTERVAL_SECONDS = 3600

TERMINAL_PHASES = frozenset({CheckPhase.COMPLETED.value, CheckPhase.FAILED.value})


def _parse_completion_time(value: Any) -> Optional[datetime]:
    """Parse an ISO 8601 completionTime; returns None if missing/unparsable.

    Naive timestamps are assumed to be UTC (the operator always writes
    timezone-aware UTC timestamps via ``get_current_time_iso``).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_check_expired(check: Dict[str, Any], ttl_hours: int, now: datetime) -> bool:
    """Return True if a HealthCheck is terminal and older than the TTL.

    A check is expired when its phase is Completed or Failed and its
    completionTime is at least ``ttl_hours`` before ``now`` (checks exactly at
    the boundary are expired). Active checks, and terminal checks with a
    missing, unparsable, or future completionTime, are never expired.
    """
    status = check.get("status") or {}
    if status.get("phase") not in TERMINAL_PHASES:
        return False
    completed_at = _parse_completion_time(status.get("completionTime"))
    if completed_at is None:
        return False
    return now - completed_at >= timedelta(hours=ttl_hours)


async def reap_completed_checks(
    k8s_api: client.CustomObjectsApi,
    ttl_hours: int,
    now: Optional[datetime] = None,
) -> int:
    """Delete expired terminal HealthChecks cluster-wide.

    Returns the number of checks deleted. Raises if the list call fails;
    individual delete failures (other than 404) are logged and skipped so one
    bad object does not block the rest of the pass.
    """
    now = now or datetime.now(timezone.utc)
    checks = await asyncio.to_thread(
        k8s_api.list_cluster_custom_object,
        group=GROUP,
        version=VERSION,
        plural=PLURAL,
    )
    items = checks.get("items", [])

    deleted = 0
    for check in items:
        if not is_check_expired(check, ttl_hours, now):
            continue
        metadata = check.get("metadata") or {}
        name = metadata.get("name")
        namespace = metadata.get("namespace")
        if not name or not namespace:
            logger.warning(
                f"Skipping expired HealthCheck with missing metadata: {metadata}"
            )
            continue
        try:
            await asyncio.to_thread(
                k8s_api.delete_namespaced_custom_object,
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=name,
            )
            deleted += 1
            logger.info(f"Reaped expired HealthCheck: {namespace}/{name}")
        except ApiException as e:
            if e.status == 404:
                # Already deleted concurrently; reaping is idempotent.
                logger.debug(f"HealthCheck {namespace}/{name} already deleted")
            else:
                logger.error(f"Failed to delete HealthCheck {namespace}/{name}: {e}")

    logger.info(
        f"Cleanup pass finished: scanned={len(items)} deleted={deleted} ttl_hours={ttl_hours}"
    )
    return deleted


def start_reaper(
    k8s_api: client.CustomObjectsApi,
    config: OperatorConfig,
) -> Optional[asyncio.Task]:
    """Start the background reaper task, or return None if cleanup is disabled."""
    if not config.cleanup_completed_checks:
        logger.info(
            "cleanup_completed_checks is disabled; completed HealthChecks will not be reaped"
        )
        return None
    logger.info(
        f"Starting completed HealthCheck reaper: ttl={config.completed_check_ttl_hours}h, "
        f"interval={REAPER_INTERVAL_SECONDS}s"
    )
    return asyncio.create_task(reaper_loop(k8s_api, config.completed_check_ttl_hours))


async def reaper_loop(
    k8s_api: client.CustomObjectsApi,
    ttl_hours: int,
    interval_seconds: float = REAPER_INTERVAL_SECONDS,
) -> None:
    """Run a reap pass immediately and then every ``interval_seconds``.

    The first pass runs at startup so a backlog accumulated while the operator
    was down (or while cleanup was disabled) is reaped right away. A failing
    pass is logged and retried on the next tick rather than killing the task.
    """
    while True:
        try:
            await reap_completed_checks(k8s_api, ttl_hours)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Cleanup pass failed; will retry on next tick", exc_info=True)
        await asyncio.sleep(interval_seconds)
