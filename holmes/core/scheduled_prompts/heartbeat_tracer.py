import logging
import time
from typing import TYPE_CHECKING, Optional

from holmes.common.env_vars import SCHEDULED_PROMPTS_HEARTBEAT_INTERVAL_SECONDS
from holmes.core.supabase_dal import RunStatus
from holmes.core.tracing import DummySpan

if TYPE_CHECKING:
    from holmes.core.scheduled_prompts.models import ScheduledPrompt
    from holmes.core.supabase_dal import SupabaseDal


class ScheduledPromptsHeartbeatSpan(DummySpan):
    """A span that sends heartbeats for scheduled prompt execution."""

    def __init__(
        self,
        sp: "ScheduledPrompt",
        dal: "SupabaseDal",
        heartbeat_interval_seconds: int = SCHEDULED_PROMPTS_HEARTBEAT_INTERVAL_SECONDS,
    ):
        """
        Args:
            sp: The scheduled prompt being executed
            dal: Database access layer for updating run status
            heartbeat_interval_seconds: Minimum seconds between heartbeat calls
        """
        self.sp = sp
        self.dal = dal
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.last_heartbeat_time = time.time()

    def start_span(self, name: Optional[str] = None, span_type=None, **kwargs):
        """Override start_span to trigger heartbeat on activity. Typically called during tool calls"""
        self._maybe_heartbeat()
        return ScheduledPromptsHeartbeatSpan(
            sp=self.sp,
            dal=self.dal,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
        )

    def log(self, *args, **kwargs):
        """Override log to trigger heartbeat on activity."""
        self._maybe_heartbeat()

    def _maybe_heartbeat(self):
        """Send heartbeat if enough time has elapsed."""
        current_time = time.time()
        if current_time - self.last_heartbeat_time >= self.heartbeat_interval_seconds:
            try:
                self.dal.update_run_status(run_id=self.sp.id, status=RunStatus.RUNNING)
                self.last_heartbeat_time = current_time
                logging.debug(f"Heartbeat for SP - {self.sp.id}")
            except Exception as e:
                logging.warning(f"Heartbeat callback failed for SP - {self.sp.id}: {e}")
