import logging
from typing import Any, Dict, Optional

import requests
from pydantic import Field, model_validator

from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


class DBADashConfig(ToolsetConfig):
    """Configuration for the DBADash Web toolset."""

    api_url: str = Field(
        title="DBADash Web URL",
        description="Base URL of the dbdash-web instance",
        examples=["https://db-monitor.shared.platform.pditechnologies.com"],
    )
    username: str = Field(
        title="Username",
        description="Username for JWT authentication",
        examples=["holmes-service"],
    )
    password: str = Field(
        title="Password",
        description="Password for JWT authentication",
        examples=["{{ env.DBDASH_PASSWORD }}"],
    )
    instance_tags: Optional[Dict[str, str]] = Field(
        default=None,
        title="Instance Tags",
        description="Filter instances by tags. Only instances matching ALL tags are visible.",
        examples=[{"project": "payments"}],
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates",
    )
    timeout_seconds: int = Field(
        default=30,
        title="Request Timeout",
        description="HTTP request timeout in seconds",
    )

    @model_validator(mode="after")
    def strip_trailing_slash(self) -> "DBADashConfig":
        if self.api_url.endswith("/"):
            self.api_url = self.api_url.rstrip("/")
        return self


class DBADashClient:
    """Thin HTTP wrapper for dbdash-web API with JWT authentication."""

    def __init__(self, config: DBADashConfig):
        self._config = config
        self._session: Optional[requests.Session] = None
        self._authenticated = False

    def _ensure_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = self._config.verify_ssl
        return self._session

    def _login(self) -> None:
        session = self._ensure_session()
        response = session.post(
            f"{self._config.api_url}/api/auth/login",
            json={
                "username": self._config.username,
                "password": self._config.password,
            },
            timeout=self._config.timeout_seconds,
            verify=self._config.verify_ssl,
        )
        response.raise_for_status()
        self._authenticated = True
        logger.debug("Successfully authenticated with dbdash-web")

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self._login()

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._ensure_authenticated()
        session = self._ensure_session()
        url = f"{self._config.api_url}{endpoint}"

        try:
            response = session.get(
                url,
                params=params,
                timeout=self._config.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.info("Got 401, re-authenticating with dbdash-web")
                self._authenticated = False
                self._login()
                response = session.get(
                    url,
                    params=params,
                    timeout=self._config.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            raise

    def health_check(self) -> Dict[str, Any]:
        return self.get("/api/health")
