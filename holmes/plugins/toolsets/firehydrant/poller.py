"""Server-side poller that runs FireHydrant AI investigations through Holmes.

The poller runs in one daemon thread for the whole server. On every cycle it
looks up the ``firehydrant/investigations`` toolset in the server's cached
tool executor, so enabling, reconfiguring or disabling the toolset (from the
config file or from the Robusta UI) takes effect on the next cycle without a
restart. While the toolset is not enabled it idles.

When the toolset is ready it lists pending investigations (oldest first),
claims each one, builds a prompt from the incident, runs it through the
server's chat function and posts the markdown report back with
``POST /v1/investigations/{id}/complete``. Investigations are processed one at
a time.
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Union

from starlette.requests import Request

from holmes.core.models import ChatRequest, ChatResponse
from holmes.core.prompt import todowrite_overrides
from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets.firehydrant.firehydrant import (
    FIREHYDRANT_TOOLSET_NAME,
    FireHydrantApiError,
    FireHydrantClient,
    FireHydrantConfig,
    FireHydrantInvestigationStatus,
    FireHydrantToolset,
    compact_incident,
)

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

    from holmes.config import Config
    from holmes.core.supabase_dal import SupabaseDal

ChatFunction = Callable[[ChatRequest, Request], Union[ChatResponse, "StreamingResponse"]]

LOG_PREFIX = "FireHydrant investigations"
INVESTIGATION_PROMPT_TEMPLATE = os.path.join(os.path.dirname(__file__), "investigation_prompt.jinja2")

# FireHydrant shows the summary as the investigation's headline; keep it short.
MAX_SUMMARY_CHARS = 500
# Pending investigations fetched per poll. The loop processes them one at a
# time, so a long first page only delays the ones at its end.
PENDING_PAGE_SIZE = 20


def summarize_analysis(analysis: str) -> str:
    """The first paragraph of the report as plain text, for FireHydrant's top-line summary."""
    for paragraph in re.split(r"\n\s*\n", analysis.strip()):
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        # A heading on its own says nothing about the outcome; the sentence under it does.
        lines = [line for line in lines if not line.startswith("#")]
        text = " ".join(lines)
        text = re.sub(r"^(?:[>\-*+]\s+|\d+[.)]\s+)+", "", text)  # blockquote, bullet, numbering marks
        text = re.sub(r"[*_`]", "", text).strip()
        if text:
            if len(text) > MAX_SUMMARY_CHARS:
                return text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
            return text
    return "Holmes investigation completed"


def build_investigation_prompt(investigation: Dict[str, Any], incident: Dict[str, Any]) -> str:
    """Render the user prompt for one investigation from the investigation and its (compacted) incident."""
    return load_and_render_prompt(
        f"file://{INVESTIGATION_PROMPT_TEMPLATE}",
        context={
            "incident": incident,
            "incident_json": json.dumps(incident, indent=2, default=str),
            "investigation_json": json.dumps(investigation, indent=2, default=str),
        },
    )


def describe_investigation(investigation: Dict[str, Any]) -> str:
    """A log-friendly label: the investigation id plus the incident it is about."""
    incident = investigation.get("incident") or {}
    label = f"investigation {investigation.get('id')}"
    if incident.get("number") is not None or incident.get("name"):
        label += f" (incident #{incident.get('number')} '{incident.get('name')}')"
    return label


class FireHydrantInvestigationsPoller:
    # Cycle length while the toolset is absent, disabled or failing; the
    # configured poll interval applies once it is ready.
    IDLE_INTERVAL_SECONDS = 60

    def __init__(
        self,
        config: "Config",
        dal: Optional["SupabaseDal"],
        chat_function: ChatFunction,
    ):
        self.config = config
        self.dal = dal
        self.chat_function = chat_function
        # Pod name in Kubernetes; part of the external_id FireHydrant shows for the claim.
        self.holmes_id = os.environ.get("HOSTNAME") or str(os.getpid())
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_state: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            logging.warning(f"{LOG_PREFIX} poller is already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="firehydrant-investigations")
        self._thread.start()
        logging.info(f"{LOG_PREFIX} poller started (holmes id: {self.holmes_id})")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logging.info(f"{LOG_PREFIX} poller stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            interval = self.IDLE_INTERVAL_SECONDS
            try:
                toolset = self._active_toolset()
                if toolset is not None:
                    interval = toolset.firehydrant_config.poll_interval_seconds
                    self.poll_once(toolset)
            except Exception:
                logging.exception(f"{LOG_PREFIX}: unexpected error in poll loop")
            self._stop_event.wait(interval)

    def _set_state(self, state: str) -> None:
        """Log the poller's state only when it changes, so an idle poller stays quiet."""
        if state != self._last_state:
            logging.info(f"{LOG_PREFIX} poller: {state}")
            self._last_state = state

    def _active_toolset(self) -> Optional[FireHydrantToolset]:
        """The FireHydrant toolset, when it is enabled, healthy and configured to poll."""
        executor = self.config.cached_tool_executor
        if executor is None:
            # The server builds its executor on the first chat or sync; build the
            # same cached one here so polling does not wait for traffic.
            executor = self.config.create_server_tool_executor(self.dal)

        toolset = next((t for t in executor.toolsets if t.name == FIREHYDRANT_TOOLSET_NAME), None)
        if not isinstance(toolset, FireHydrantToolset) or not toolset.enabled:
            self._set_state(f"waiting for toolset '{FIREHYDRANT_TOOLSET_NAME}' to be enabled")
            return None
        if toolset.needs_initialization:
            toolset.lazy_initialize(silent=True)
        if toolset.status != ToolsetStatusEnum.ENABLED or toolset.config is None:
            self._set_state(f"toolset '{FIREHYDRANT_TOOLSET_NAME}' is not ready: {toolset.error}")
            return None
        config = toolset.firehydrant_config
        if not config.poll_investigations:
            self._set_state("polling is disabled in the toolset config (poll_investigations: false)")
            return None
        self._set_state(
            f"polling {config.api_url} every {config.poll_interval_seconds}s for pending investigations "
            f"(model: {config.model or 'default'})"
        )
        return toolset

    def poll_once(self, toolset: FireHydrantToolset) -> int:
        """Claim and run every pending investigation on the first page. Returns how many were claimed."""
        client = toolset.client
        config = toolset.firehydrant_config
        try:
            response = client.list_investigations(
                status=FireHydrantInvestigationStatus.PENDING, per_page=PENDING_PAGE_SIZE
            )
        except Exception as e:
            logging.warning(f"{LOG_PREFIX}: failed to list pending investigations: {e}")
            return 0

        pending = [i for i in (response.get("data") or []) if isinstance(i, dict) and i.get("id")]
        if not pending:
            logging.debug(f"{LOG_PREFIX}: no pending investigations")
            return 0

        logging.info(f"{LOG_PREFIX}: found {len(pending)} pending investigation(s)")
        claimed = 0
        for investigation in pending:
            if self._stop_event.is_set():
                break
            if self._handle_investigation(client, config, investigation):
                claimed += 1
        return claimed

    def _handle_investigation(
        self,
        client: FireHydrantClient,
        config: FireHydrantConfig,
        investigation: Dict[str, Any],
    ) -> bool:
        """Claim one investigation, run it and report back. Returns whether the claim succeeded."""
        investigation_id = str(investigation["id"])
        label = describe_investigation(investigation)
        external_id = f"holmes-{self.holmes_id}-{uuid.uuid4()}"

        try:
            client.claim_investigation(investigation_id, external_id=external_id)
        except FireHydrantApiError as e:
            if e.status_code == 409:
                logging.info(f"{LOG_PREFIX}: {label} was already claimed by another engine, skipping")
            else:
                logging.error(f"{LOG_PREFIX}: failed to claim {label}: {e}")
            return False
        except Exception as e:
            logging.error(f"{LOG_PREFIX}: failed to claim {label}: {e}")
            return False

        logging.info(f"{LOG_PREFIX}: claimed {label} as {external_id}, starting investigation")
        start = time.perf_counter()
        try:
            incident = self._fetch_incident(client, investigation)
            prompt = build_investigation_prompt(investigation, incident)
            analysis = self._run_holmes(prompt, config, investigation_id)
            elapsed = time.perf_counter() - start
            if self._is_canceled(client, investigation_id):
                logging.info(
                    f"{LOG_PREFIX}: {label} was canceled while Holmes investigated ({elapsed:.1f}s), not posting the result"
                )
                return True
            summary = summarize_analysis(analysis)
            client.complete_investigation(
                investigation_id,
                status=FireHydrantInvestigationStatus.COMPLETED,
                summary=summary,
                markdown=analysis,
            )
            logging.info(f"{LOG_PREFIX}: completed {label} in {elapsed:.1f}s: {summary}")
        except Exception as e:
            elapsed = time.perf_counter() - start
            logging.exception(f"{LOG_PREFIX}: {label} failed after {elapsed:.1f}s: {e}")
            self._report_failure(client, investigation_id, label, str(e))
        return True

    def _fetch_incident(self, client: FireHydrantClient, investigation: Dict[str, Any]) -> Dict[str, Any]:
        """The investigation's incident, compacted. Falls back to the summary embedded in the investigation."""
        summary = investigation.get("incident") or {}
        incident_id = summary.get("id")
        if not incident_id:
            logging.warning(f"{LOG_PREFIX}: {describe_investigation(investigation)} has no incident attached")
            return dict(summary)
        try:
            return compact_incident(client.get_incident(str(incident_id)))
        except Exception as e:
            logging.warning(
                f"{LOG_PREFIX}: could not fetch incident {incident_id} for {describe_investigation(investigation)}, "
                f"investigating with the summary only: {e}"
            )
            return dict(summary)

    def _run_holmes(self, prompt: str, config: FireHydrantConfig, investigation_id: str) -> str:
        chat_request = ChatRequest(
            ask=prompt,
            model=config.model,
            conversation_history=None,
            stream=False,
            additional_system_prompt=config.additional_system_prompt,
            # Fast mode: the report lands in ChatResponse.analysis rather than
            # behind a trailing TodoWrite call.
            behavior_controls={
                component.value: enabled for component, enabled in todowrite_overrides(False).items()
            },
            request_type="firehydrant_investigation",
            request_source="firehydrant",
            source_ref=investigation_id,
        )
        empty_request = Request(scope={"type": "http", "headers": []})
        response = self.chat_function(chat_request, empty_request)
        if not isinstance(response, ChatResponse):
            raise RuntimeError(f"Unexpected response type from Holmes: {type(response).__name__}")
        if not response.analysis or not response.analysis.strip():
            raise RuntimeError("Holmes returned an empty analysis")
        return response.analysis

    def _is_canceled(self, client: FireHydrantClient, investigation_id: str) -> bool:
        try:
            current = client.get_investigation(investigation_id)
        except Exception as e:
            logging.warning(
                f"{LOG_PREFIX}: could not re-read investigation {investigation_id} before completing it, "
                f"posting the result anyway: {e}"
            )
            return False
        return current.get("status") == FireHydrantInvestigationStatus.CANCELED

    def _report_failure(self, client: FireHydrantClient, investigation_id: str, label: str, error: str) -> None:
        try:
            client.complete_investigation(
                investigation_id,
                status=FireHydrantInvestigationStatus.FAILED,
                summary="Holmes could not complete the investigation",
                error=error,
            )
            logging.info(f"{LOG_PREFIX}: reported {label} as failed")
        except Exception:
            logging.error(f"{LOG_PREFIX}: could not report {label} as failed", exc_info=True)
