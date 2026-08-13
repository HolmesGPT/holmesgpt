import logging
from functools import cache
from typing import Any, Dict, Optional

import requests  # type: ignore
from pydantic import BaseModel, ConfigDict
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from holmes.common.env_vars import ROBUSTA_API_ENDPOINT

HOLMES_GET_INFO_URL = f"{ROBUSTA_API_ENDPOINT}/api/holmes/get_info"
TIMEOUT = 0.5

# 429/5xx (gateway blips, overload) heal on retry; 4xx (bad token, unknown
# account) doesn't, and retrying it would only delay startup.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
FETCH_MODELS_ATTEMPTS = 5

logger = logging.getLogger(__name__)


class HolmesInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    latest_version: Optional[str] = None


class RobustaModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str
    holmes_args: Optional[dict[str, Any]] = None
    is_default: bool = False


class RobustaModelsResponse(BaseModel):
    models: Dict[str, RobustaModel]


def _is_retryable_fetch_error(exc: BaseException) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        return (
            exc.response is not None
            and exc.response.status_code in _RETRYABLE_STATUS_CODES
        )
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )


# The model list is fetched once, at startup: losing that single request to a
# transient relay/gateway blip degrades the agent to the legacy single-model
# fallback for the pod's whole life (ROB-795). Retries stay bounded because
# they block boot — the liveness probe kills the pod after ~130s without a
# served /healthz.
@retry(
    retry=retry_if_exception(_is_retryable_fetch_error),
    stop=stop_after_attempt(FETCH_MODELS_ATTEMPTS),
    wait=wait_exponential(min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _request_robusta_models(account_id: str, token: str) -> RobustaModelsResponse:
    resp = requests.post(
        f"{ROBUSTA_API_ENDPOINT}/api/llm/models/v2",
        json={"session_token": token, "account_id": account_id},
        timeout=10,
    )
    resp.raise_for_status()
    return RobustaModelsResponse(models=resp.json())


def fetch_robusta_models(
    account_id: str, token: str
) -> Optional[RobustaModelsResponse]:
    try:
        return _request_robusta_models(account_id, token)
    except Exception:
        logging.exception("Failed to fetch robusta models for account")
        return None


@cache
def fetch_holmes_info() -> Optional[HolmesInfo]:
    try:
        response = requests.get(HOLMES_GET_INFO_URL, timeout=TIMEOUT)
        response.raise_for_status()
        result = response.json()
        return HolmesInfo(**result)
    except Exception:
        return None
