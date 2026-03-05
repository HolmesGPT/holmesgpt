import logging
import os
import threading
import time
from typing import Optional

import jwt
import requests

logger = logging.getLogger(__name__)

# Refresh token 5 minutes before expiry
TOKEN_REFRESH_BUFFER_SECONDS = 300


class GitHubAppTokenManager:
    """Manages GitHub App installation tokens with automatic caching and refresh.

    Generates short-lived installation tokens from GitHub App credentials
    (APP_ID, INSTALLATION_ID, PRIVATE_KEY) and caches them until they are
    close to expiring.

    Thread-safe: concurrent callers will not trigger duplicate token refreshes.
    """

    _instance: Optional["GitHubAppTokenManager"] = None
    _lock = threading.Lock()

    def __init__(self, app_id: str, installation_id: str, private_key: str):
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key = private_key
        self._token: Optional[str] = None
        self._expiry: float = 0.0
        self._refresh_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> Optional["GitHubAppTokenManager"]:
        """Create a manager from environment variables, or return None if not configured."""
        app_id = os.environ.get("GITHUB_APP_ID")
        installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
        private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")

        if not all([app_id, installation_id, private_key]):
            return None

        return cls(
            app_id=app_id,  # type: ignore[arg-type]
            installation_id=installation_id,  # type: ignore[arg-type]
            private_key=private_key,  # type: ignore[arg-type]
        )

    @classmethod
    def get_instance(cls) -> Optional["GitHubAppTokenManager"]:
        """Get or create the singleton instance. Returns None if env vars are not set."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls.from_env()
        return cls._instance

    def _generate_jwt(self) -> str:
        """Generate a JWT signed with the GitHub App private key."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds in the past for clock drift
            "exp": now + 600,  # Expires in 10 minutes (GitHub maximum)
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def _refresh_token(self) -> None:
        """Exchange a JWT for a GitHub installation access token."""
        encoded_jwt = self._generate_jwt()

        response = requests.post(
            f"https://api.github.com/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {encoded_jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        self._token = data["token"]

        # Parse expiry from ISO format (e.g. "2024-01-01T12:00:00Z")
        from datetime import datetime, timezone

        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        self._expiry = expires_at.timestamp()

        logger.info("GitHub App installation token refreshed, expires at %s", data["expires_at"])

    def get_token(self) -> str:
        """Get a valid installation token, refreshing if necessary.

        Thread-safe: only one thread will perform the refresh.
        """
        if self._token and time.time() < self._expiry - TOKEN_REFRESH_BUFFER_SECONDS:
            return self._token

        with self._refresh_lock:
            # Double-check after acquiring lock
            if self._token and time.time() < self._expiry - TOKEN_REFRESH_BUFFER_SECONDS:
                return self._token
            self._refresh_token()

        return self._token  # type: ignore[return-value]


def ensure_github_app_token_env() -> None:
    """If GitHub App credentials are configured, generate an installation token
    and set it as GITHUB_TOKEN in the environment.

    This should be called early in the application lifecycle, before
    environment variable substitution resolves MCP configs.
    """
    # Don't override an existing GITHUB_TOKEN
    if os.environ.get("GITHUB_TOKEN"):
        return

    manager = GitHubAppTokenManager.get_instance()
    if manager is None:
        return

    try:
        token = manager.get_token()
        os.environ["GITHUB_TOKEN"] = token
        logger.info("Set GITHUB_TOKEN from GitHub App installation token")
    except Exception:
        logger.warning("Failed to generate GitHub App installation token", exc_info=True)


def refresh_github_app_token_env() -> None:
    """Refresh the GitHub App token in the environment if it's close to expiring.

    Call this before operations that will use the token (e.g., before MCP connections).
    """
    manager = GitHubAppTokenManager.get_instance()
    if manager is None:
        return

    try:
        token = manager.get_token()
        os.environ["GITHUB_TOKEN"] = token
    except Exception:
        logger.warning("Failed to refresh GitHub App installation token", exc_info=True)
