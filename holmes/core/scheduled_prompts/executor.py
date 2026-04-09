import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pydantic import ValidationError
from starlette.requests import Request

from holmes import get_version
from holmes.common.env_vars import (
    ROBUSTA_UI_DOMAIN,
    SCHEDULED_PROMPTS_ACTIVE_POLL_INTERVAL_SECONDS,
    SCHEDULED_PROMPTS_INACTIVE_POLL_INTERVAL_SECONDS,
)
from holmes.core.models import ChatRequest, ChatResponse
from holmes.core.scheduled_prompts.heartbeat_tracer import (
    ScheduledPromptsHeartbeatSpan,
)
from holmes.core.scheduled_prompts.models import ScheduledPrompt
from holmes.core.supabase_dal import RunStatus

# to prevent circular imports due to type hints
if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

    from holmes.config import Config
    from holmes.core.supabase_dal import SupabaseDal

ChatFunction = Callable[[ChatRequest, Request], Union["ChatResponse", "StreamingResponse"]]

ADDITIONAL_SYSTEM_PROMPT_URL = f"{ROBUSTA_UI_DOMAIN}/api/additional-system-prompt.json"

class ScheduledPromptsExecutor:
    def __init__(
        self,
        dal: "SupabaseDal",
        config: "Config",
        chat_function: ChatFunction,
    ):
        self.dal = dal
        self.config = config
        self.chat_function = chat_function
        self.running = False
        self.thread: Optional[threading.Thread] = None
        # this is pod name in kubernetes
        self.holmes_id = os.environ.get("HOSTNAME") or str(os.getpid())
        # Dynamic polling interval based on whether account has scheduled prompts
        self.poll_interval_seconds = SCHEDULED_PROMPTS_INACTIVE_POLL_INTERVAL_SECONDS

    def start(self):
        if not self.dal.enabled:
            logging.info(
                "ScheduledPromptsExecutor not started - Supabase DAL not enabled"
            )
            return

        if self.running:
            logging.warning("ScheduledPromptsExecutor is already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logging.info("ScheduledPromptsExecutor started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logging.info("ScheduledPromptsExecutor stopped")

    def _run_loop(self):
        while self.running:
            try:
                had_payload = self._process_next_prompt()
                if not had_payload:
                    # Update polling interval based on current state (may change if prompt deffinition added/removed)
                    self._update_poll_interval()
                    time.sleep(self.poll_interval_seconds)
            except Exception as exc:
                logging.exception(
                    "Error in ScheduledPromptsExecutor loop: %s", exc, exc_info=True
                )

    def _update_poll_interval(self):
        """
        Update the polling interval based on whether the account has scheduled prompts.
        Only logs when the interval actually changes to avoid log spam.
        """
        has_scheduled_prompts = self.dal.has_scheduled_prompt_definitions()
        new_interval = (
            SCHEDULED_PROMPTS_ACTIVE_POLL_INTERVAL_SECONDS
            if has_scheduled_prompts
            else SCHEDULED_PROMPTS_INACTIVE_POLL_INTERVAL_SECONDS
        )

        if new_interval != self.poll_interval_seconds:
            old_interval = self.poll_interval_seconds
            self.poll_interval_seconds = new_interval
            logging.info(
                f"Polling interval changed from {old_interval}s to {new_interval}s "
                f"(account {'has' if has_scheduled_prompts else 'has no'} scheduled prompts)"
            )

    def _process_next_prompt(self) -> bool:
        """
        Process the next scheduled prompt, if available.

        Returns:
            bool: True if a payload was found and processed, False if no payload available.
        """
        payload = self.dal.claim_scheduled_prompt_run(self.holmes_id)
        if not payload:
            return False

        try:
            sp = ScheduledPrompt(**payload)
        except ValidationError as exc:
            # due to the rpc call to supabase this row will not be pulled again on the next call of claim_scheduled_prompt_run so there is no worry of an endless loop here
            logging.exception(
                "Skipping invalid scheduled prompt payload: %s",
                exc,
                exc_info=True,
            )
            # Mark as failed_no_retry since the payload is invalid and retrying won't help
            run_id = payload.get("id") if isinstance(payload, dict) else None
            if run_id:
                self.dal.update_run_status(
                    run_id=run_id,
                    status=RunStatus.FAILED_NO_RETRY,
                    msg=f"Invalid scheduled prompt payload: {str(exc)}",
                )

            # Return True since we did find a payload, even if it was invalid
            return True

        try:
            self._execute_scheduled_prompt(sp)
        except Exception as exc:
            logging.exception(
                "Error executing scheduled %s prompt: %s",
                sp.id,
                exc,
                exc_info=True,
            )
            self._finish_run(
                status=RunStatus.FAILED,
                result={"error": str(exc)},
                sp=sp,
            )

        return True

    def _execute_scheduled_prompt(self, sp: ScheduledPrompt):
        run_id = sp.id
        available_models = self.config.get_models_list()
        if sp.model_name not in available_models:
            error_msg = f"Model '{sp.model_name}' not found in available models: {available_models}"
            logging.warning(
                "Pending run %s has invalid model_name '%s', marking as failed",
                run_id,
                sp.model_name,
            )
            self._finish_run(
                status=RunStatus.FAILED,
                result={"error": error_msg},
                sp=sp,
            )
            return

        logging.info(
            "Found pending run %s, executing with model %s", run_id, sp.model_name
        )
        self._execute_prompt(sp)
        logging.info("Successfully completed run %s", run_id)

    def _execute_prompt(
        self,
        sp: ScheduledPrompt,
    ):
        start = time.perf_counter()
        additional_system_prompt = self._fetch_additional_system_prompt(
            sp.prompt.get("additional_system_prompt")
        )

        # Create heartbeat span
        heartbeat_span = ScheduledPromptsHeartbeatSpan(sp=sp, dal=self.dal)

        # Create chat request with heartbeat span
        chat_request = ChatRequest(
            ask=self._extract_prompt_text(sp.prompt),
            model=sp.model_name,
            conversation_history=None,
            stream=False,
            additional_system_prompt=additional_system_prompt,
            trace_span=heartbeat_span,
        )

        empty_request = Request(scope={"type": "http", "headers": []})
        response = self.chat_function(chat_request, empty_request)
        duration_seconds = time.perf_counter() - start

        if isinstance(response, ChatResponse):
            response.metadata = dict(response.metadata or {})
            response.metadata["duration_seconds"] = duration_seconds

        result_data = (
            response.model_dump() if isinstance(response, ChatResponse) else {}
        )

        self._finish_run(status=RunStatus.COMPLETED, result=result_data, sp=sp)

        return response

    def _fetch_additional_system_prompt(
        self, fallback: Optional[str] = None
    ) -> Optional[str]:
        """
        Fetches the additional system prompt from the Robusta platform.
        Falls back to the provided value if the fetch fails.
        """
        try:
            with urlopen(ADDITIONAL_SYSTEM_PROMPT_URL, timeout=10) as resp:
                if resp.status != 200:
                    logging.warning(
                        "Failed to fetch additional system prompt, status: %s",
                        resp.status,
                    )
                    return fallback
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("additional_system_prompt", fallback)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            logging.warning(
                "Error fetching additional system prompt, using fallback: %s", exc
            )
            return fallback

    def _finish_run(
        self,
        status: RunStatus,
        result: dict,
        sp: ScheduledPrompt,
    ) -> None:
        self.dal.finish_scheduled_prompt_run(
            status=status,
            result=result,
            run_id=sp.id,
            scheduled_prompt_definition_id=sp.scheduled_prompt_definition_id,
            version=get_version(),
            metadata=sp.metadata,
        )

    def _extract_prompt_text(self, prompt: Union[str, dict]) -> str:
        """
        Extracts the prompt text from the prompt.
        Any additional changes to the prompt object or how we refactor it in the future should be handled here.
        """
        if isinstance(prompt, dict):
            raw = prompt.get("raw_prompt")
            if raw:
                return raw
        return str(prompt)
