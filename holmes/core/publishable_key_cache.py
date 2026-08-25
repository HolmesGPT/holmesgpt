import logging
import threading
import time
from typing import Optional

import requests

from holmes.common.env_vars import ROBUSTA_API_ENDPOINT

PUBLISHABLE_KEY_CACHE_TTL_SECONDS = 24 * 60 * 60
FETCH_TIMEOUT_SECONDS = 10


class PublishableKeyCache:
    """Caches the api key served by relay.

    A key is stored only after it was used successfully; a cached key that
    stops working is invalidated so the next call fetches a fresh one.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._key: Optional[str] = None
        self._fetched_at: float = 0.0

    def get_cached_key(self) -> Optional[str]:
        with self._lock:
            if (
                self._key
                and time.time() - self._fetched_at < PUBLISHABLE_KEY_CACHE_TTL_SECONDS
            ):
                return self._key
        return None

    def store(self, key: str):
        with self._lock:
            self._key = key
            self._fetched_at = time.time()

    def invalidate(self):
        with self._lock:
            self._key = None

    def fetch_key(self, account_id: str, cluster: str) -> Optional[str]:
        from holmes import get_version

        try:
            response = requests.get(
                f"{ROBUSTA_API_ENDPOINT}/api/config/supabase-keys",
                params={
                    "account_id": account_id,
                    "cluster": cluster,
                    "component": "holmes",
                    "component_version": get_version(),
                },
                timeout=FETCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json().get("api_key") or None
        except Exception as e:
            logging.warning(
                f"Failed to fetch api key from relay: {e}. Using the locally configured key"
            )
            return None


publishable_key_cache = PublishableKeyCache()
