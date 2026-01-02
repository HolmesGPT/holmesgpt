import logging
from functools import cache
from typing import Any, Dict, Optional

import requests  # type: ignore
from pydantic import BaseModel, ConfigDict

from holmes.common.env_vars import ROBUSTA_API_ENDPOINT

HOLMES_GET_INFO_URL = f"{ROBUSTA_API_ENDPOINT}/api/holmes/get_info"
TIMEOUT = 0.5


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


def fetch_robusta_models(
    account_id: str, token: str
) -> Optional[RobustaModelsResponse]:
    try:
        session_request = {"session_token": token, "account_id": account_id}
        resp = requests.post(
            f"{ROBUSTA_API_ENDPOINT}/api/llm/models/v2",
            json=session_request,
            timeout=10,
        )
        resp.raise_for_status()
        response_json = resp.json()
        return RobustaModelsResponse(**{"models": response_json})
    except Exception:
        logging.exception("Failed to fetch robusta models")
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
