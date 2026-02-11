import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from kubernetes import client

from holmes_operator.models import ScheduledHealthCheckSpec
from holmes_operator.scheduler.job_executor import execute_scheduled_check

logger = logging.getLogger(__name__)


class SchedulerManager:
    """
    Manages APScheduler lifecycle and job registry.

    Responsibilities:
    - Register/update/remove scheduled jobs
    - Load existing schedules on startup
    - Handle catchup for missed executions
    """

    def __init__(self, timezone_str: str, k8s_api: client.CustomObjectsApi):
        """
        Initialize scheduler manager.

        Args:
            timezone_str: Timezone for cron scheduling (e.g., "UTC", "America/New_York")
            k8s_api: Kubernetes API client for querying ScheduledHealthCheck resources
        """
        self.k8s_api = k8s_api
        self.scheduler = AsyncIOScheduler(
            timezone=timezone_str,
            jobstores={"default": MemoryJobStore()},
            job_defaults={
                "coalesce": True,  # Combine multiple missed executions into one
                "max_instances": 1,  # Only one instance of each job at a time
                "misfire_grace_time": 300,  # 5 minutes grace period for missed runs
            },
        )
        # Map of {namespace/name: job_id}
        self.job_registry: Dict[str, str] = {}
        logger.info(f"Initialized SchedulerManager with timezone: {timezone_str}")

    async def start(self):
        """Start the scheduler and load existing schedules."""
        logger.info("Starting scheduler...")
        self.scheduler.start()
        await self._load_existing_schedules()
        logger.info("Scheduler started successfully")

    async def stop(self):
        """Gracefully shut down the scheduler."""
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped successfully")

    async def add_schedule(
        self,
        name: str,
        namespace: str,
        cron_expr: str,
        spec: ScheduledHealthCheckSpec,
        scheduled_uid: str,
    ) -> str:
        """
        Register a cron job with APScheduler.

        Args:
            name: ScheduledHealthCheck name
            namespace: ScheduledHealthCheck namespace
            cron_expr: Cron expression (e.g., "*/5 * * * *")
            spec: ScheduledHealthCheck spec
            scheduled_uid: UID of the ScheduledHealthCheck resource (for ownerReferences)
            check_catchup: Whether to check for and execute missed runs

        Returns:
            Job ID

        Raises:
            ValueError: If cron expression is invalid
        """
        key = f"{namespace}/{name}"

        try:
            trigger = CronTrigger.from_crontab(cron_expr)
        except Exception as e:
            logger.error(f"Invalid cron expression for {key}: {e}")
            raise ValueError(f"Invalid cron expression '{cron_expr}': {e}") from e

        try:
            # Add job to scheduler
            job = self.scheduler.add_job(
                func=execute_scheduled_check,
                trigger=trigger,
                args=(name, namespace, spec, scheduled_uid, self.k8s_api),
                id=key,
                replace_existing=True,
                name=f"ScheduledHealthCheck {key}",
            )

            self.job_registry[key] = job.id
            logger.info(f"Registered schedule for {key} with cron: {cron_expr}")
            return job.id

        except Exception as e:
            logger.error(f"Failed to register schedule for {key}: {e}", exc_info=True)
            raise

    async def update_schedule(
        self,
        name: str,
        namespace: str,
        cron_expr: str,
        spec: ScheduledHealthCheckSpec,
        scheduled_uid: str,
    ) -> str:
        """
        Update an existing schedule by removing and re-adding it.

        Args:
            name: ScheduledHealthCheck name
            namespace: ScheduledHealthCheck namespace
            cron_expr: New cron expression
            spec: Updated ScheduledHealthCheck spec
            scheduled_uid: UID of the ScheduledHealthCheck resource

        Returns:
            New job ID
        """
        key = f"{namespace}/{name}"
        logger.info(f"Updating schedule for {key}")

        # Remove old job if exists
        await self.remove_schedule(name, namespace)

        # Add new job
        return await self.add_schedule(name, namespace, cron_expr, spec, scheduled_uid)

    async def remove_schedule(self, name: str, namespace: str):
        """
        Unregister a job from APScheduler.

        Args:
            name: ScheduledHealthCheck name
            namespace: ScheduledHealthCheck namespace
        """
        key = f"{namespace}/{name}"

        if key in self.job_registry:
            job_id = self.job_registry[key]
            try:
                self.scheduler.remove_job(job_id)
                logger.info(f"Removed schedule for {key}")
            except Exception as e:
                logger.error(f"Failed to remove job {job_id}: {e}", exc_info=True)
            finally:
                # Always clean up registry entry to avoid stale entries
                self.job_registry.pop(key, None)
        else:
            logger.debug(f"No schedule found for {key} to remove")

    async def _load_existing_schedules(self):
        logger.info("Loading existing ScheduledHealthCheck resources...")

        try:
            # List all ScheduledHealthCheck resources across all namespaces
            resources = await asyncio.to_thread(
                self.k8s_api.list_cluster_custom_object,
                group="holmesgpt.dev",
                version="v1alpha1",
                plural="scheduledhealthchecks",
            )

            items = resources.get("items", [])
            logger.info(f"Found {len(items)} ScheduledHealthCheck resources")

            for resource in items:
                metadata = resource.get("metadata", {})
                spec = resource.get("spec", {})

                name = metadata.get("name")
                namespace = metadata.get("namespace")
                uid = metadata.get("uid")
                enabled = spec.get("enabled", True)

                if not enabled:
                    logger.debug(f"Skipping disabled schedule: {namespace}/{name}")
                    continue

                try:
                    # Parse spec
                    scheduled_spec = ScheduledHealthCheckSpec(**spec)

                    # Register schedule with catchup check
                    await self.add_schedule(
                        name=name,
                        namespace=namespace,
                        cron_expr=scheduled_spec.schedule,
                        spec=scheduled_spec,
                        scheduled_uid=uid,
                    )

                except Exception as e:
                    logger.error(f"Failed to load schedule {namespace}/{name}: {e}")

        except Exception as e:
            logger.error(f"Failed to load existing schedules: {e}")

    async def _check_and_catchup(
        self,
        name: str,
        namespace: str,
        cron_expr: str,
        spec: ScheduledHealthCheckSpec,
        scheduled_uid: str,
    ):
        """
        Check if a scheduled execution was missed and execute immediately if so.

        Args:
            name: ScheduledHealthCheck name
            namespace: ScheduledHealthCheck namespace
            cron_expr: Cron expression
            spec: ScheduledHealthCheck spec
            scheduled_uid: UID of the ScheduledHealthCheck resource
        """
        try:
            # Get current resource to check lastScheduleTime
            resource = await asyncio.to_thread(
                self.k8s_api.get_namespaced_custom_object,
                group="holmesgpt.dev",
                version="v1alpha1",
                namespace=namespace,
                plural="scheduledhealthchecks",
                name=name,
            )

            status = resource.get("status", {})
            last_schedule_time_str = status.get("lastScheduleTime")

            # Calculate when the check should have run last
            trigger = CronTrigger.from_crontab(cron_expr)
            now = datetime.now(timezone.utc)

            # If there's a lastScheduleTime, use it as the reference point
            # Otherwise, check if it should fire now
            if last_schedule_time_str:
                last_schedule_time = datetime.fromisoformat(
                    last_schedule_time_str.replace("Z", "+00:00")
                )
                # Check if there should have been an execution between lastScheduleTime and now
                previous_fire = trigger.get_next_fire_time(last_schedule_time, now)
            else:
                # First run - execute immediately
                logger.info(
                    f"First run detected for {namespace}/{name}, executing immediately"
                )
                await execute_scheduled_check(
                    name=name,
                    namespace=namespace,
                    spec=spec,
                    scheduled_uid=scheduled_uid,
                    k8s_api=self.k8s_api,
                )
                return

            # If there's a scheduled time that we missed, execute immediately
            if previous_fire and previous_fire < now:
                logger.info(
                    f"Missed execution detected for {namespace}/{name}: "
                    f"should have run at {previous_fire}, executing catchup now"
                )
                # Execute immediately (catchup)
                await execute_scheduled_check(
                    name=name,
                    namespace=namespace,
                    spec=spec,
                    scheduled_uid=scheduled_uid,
                    k8s_api=self.k8s_api,
                )

        except Exception as e:
            logger.error(f"Error during catchup check for {namespace}/{name}: {e}")
