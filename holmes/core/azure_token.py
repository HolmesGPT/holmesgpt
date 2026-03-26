import logging
import threading
import time
from typing import Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from holmes.common.env_vars import AZURE_COGNITIVE_SERVICES_SCOPE

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_SECONDS = 3600  # 1 hour

_lock = threading.Lock()
_cached_token: Optional[str] = None
_token_timestamp: float = 0.0


def get_azure_ad_token() -> str:
    """Return a cached Azure AD bearer token, refreshing if expired.

    The token is obtained via ``get_bearer_token_provider(DefaultAzureCredential(), ...)``
    and cached for up to TOKEN_EXPIRY_SECONDS (1 hour).
    """
    global _cached_token, _token_timestamp

    with _lock:
        now = time.monotonic()
        if _cached_token is not None and (now - _token_timestamp) < TOKEN_EXPIRY_SECONDS:
            return _cached_token

        logger.info("Fetching new Azure AD token for Azure OpenAI authentication")
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(credential, AZURE_COGNITIVE_SERVICES_SCOPE)
        _cached_token = token_provider()
        _token_timestamp = now
        return _cached_token
