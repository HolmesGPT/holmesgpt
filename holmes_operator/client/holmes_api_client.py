"""HTTP client for calling Holmes API servers."""

import logging
import os
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from holmes_operator.models import CheckResponse

logger = logging.getLogger(__name__)


class HolmesAPIClient:
    """
    HTTP client for calling Holmes API servers.

    Handles:
    - Async HTTP requests
    - Retry logic with exponential backoff
    - Timeout management
    - Error handling and logging
    """

    def __init__(self, base_url: str, timeout: int = 300, api_key_file: str = ""):
        """
        Initialize the Holmes API client.

        Args:
            base_url: Base URL of the Holmes API server (e.g., "http://holmes-api:80")
            timeout: Default timeout for requests in seconds
            api_key_file: File holding the API key to send. Re-read on every
                request so a rotated Secret is used without a restart.
                The HOLMES_API_KEY env var takes precedence when set.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key_file = api_key_file
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=timeout, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        logger.info(f"Initialized Holmes API client for {self.base_url}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def execute_check(
        self,
        check_name: str,
        query: str,
        timeout: int,
        mode: str,
        destinations: list,
        model: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> CheckResponse:
        """
        Execute a health check via Holmes API.

        Endpoint: POST /api/checks/execute

        Args:
            check_name: Name of the check for tracking
            query: Natural language question about system health
            timeout: Execution timeout in seconds
            mode: "alert" or "monitor"
            destinations: List of destination configurations
            model: Optional model override
            namespace: Namespace the check runs in (forwarded to alert payloads)

        Returns:
            CheckResponse

        Raises:
            httpx.HTTPError: If the API request fails after retries
        """
        url = f"{self.base_url}/api/checks/execute"
        payload = {
            "name": check_name,
            "query": query,
            "timeout": timeout,
            "mode": mode,
            "destinations": destinations,
        }
        if model:
            payload["model"] = model
        if namespace:
            payload["namespace"] = namespace

        logger.info(
            f"Executing check '{check_name}' via Holmes API: {url}",
            extra={
                "check_name": check_name,
                "namespace": namespace,
                "query": query[:100],
                "mode": mode,
                "timeout": timeout,
            },
        )

        try:
            response = await self.client.post(url, json=payload, headers=self._auth_headers())
            response.raise_for_status()
            result = response.json()

            logger.info(
                f"Check '{check_name}' completed with status: {result.get('status')}",
                extra={
                    "check_name": check_name,
                    "status": result.get("status"),
                    "duration": result.get("duration"),
                },
            )

            return CheckResponse(**result)

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Holmes API returned error for check '{check_name}': {e.response.status_code}",
                exc_info=True,
                extra={
                    "check_name": check_name,
                    "status_code": e.response.status_code,
                    "response_text": e.response.text[:500],
                },
            )
            raise

        except httpx.RequestError as e:
            logger.error(
                f"Failed to connect to Holmes API for check '{check_name}': {e}",
                exc_info=True,
                extra={"check_name": check_name, "error": str(e)},
            )
            raise

    def _api_key(self) -> str:
        key = os.environ.get("HOLMES_API_KEY", "").strip()
        if key or not self.api_key_file:
            return key
        try:
            with open(self.api_key_file) as f:
                return f.read().strip()
        except OSError:
            # Not created yet (optional Secret volume) or unreadable: send no
            # key and let Holmes answer 401, which the caller logs.
            return ""

    def _auth_headers(self) -> dict:
        key = self._api_key()
        return {"X-API-Key": key} if key else {}

    async def close(self):
        """Close the HTTP client and cleanup resources."""
        await self.client.aclose()
        logger.info("Closed Holmes API client")
