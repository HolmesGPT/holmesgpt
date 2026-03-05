import logging
import os
import threading
import time
from typing import Optional

import jwt
import requests

logger = logging.getLogger(__name__)

# How often to refresh the token (seconds). Default: 30 minutes.
# Configurable via GITHUB_APP_TOKEN_REFRESH_INTERVAL_SECONDS env var.
TOKEN_REFRESH_INTERVAL_SECONDS = int(
    os.environ.get("GITHUB_APP_TOKEN_REFRESH_INTERVAL_SECONDS", "1800")
)


def _mask_token(token: str) -> str:
    """Return first 4 and last 4 chars of a token for debug logging."""
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


class GitHubAppTokenManager:
    """Manages GitHub App installation tokens with automatic refresh.

    Generates short-lived installation tokens from GitHub App credentials
    (APP_ID, INSTALLATION_ID, PRIVATE_KEY). A background daemon thread
    refreshes the token at a fixed interval and updates
    os.environ["AUTO_GENERATED_GITHUB_TOKEN"] so that the relevant mcp servers
    (e.g. extra_headers) always read a valid token.
    """

    _instance: Optional["GitHubAppTokenManager"] = None
    _lock = threading.Lock()

    def __init__(self, app_id: str, installation_id: str, private_key: str):
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key = private_key
        self._refresh_thread_started = False

    @staticmethod
    def has_github_app_env_vars() -> bool:
        """Check if all required GitHub App environment variables are set."""
        return all(
            os.environ.get(k)
            for k in ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY")
        )

    @classmethod
    def from_env(cls) -> Optional["GitHubAppTokenManager"]:
        """Create a manager from environment variables, or return None if not configured."""
        app_id = os.environ.get("GITHUB_APP_ID")
        installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
        private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")

        if not all([app_id, installation_id, private_key]):
            return None

        # CI/CD systems and secret stores may inject the private key with literal
        # "\n" instead of actual newlines, which breaks JWT signing.
        private_key = private_key.replace("\\n", "\n")  # type: ignore[union-attr]

        return cls(
            app_id=app_id,  # type: ignore[arg-type]
            installation_id=installation_id,  # type: ignore[arg-type]
            private_key=private_key,  # type: ignore[arg-type]
        )

    @classmethod
    def get_instance(cls) -> Optional["GitHubAppTokenManager"]:
        """Get or create the singleton instance. Returns None if env vars are not set."""
        if cls._instance is not None:
            return cls._instance
        if not cls.has_github_app_env_vars():
            return None
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

    def refresh_token(self) -> str:
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
        token = data["token"]

        logger.info(
            "GitHub App installation token refreshed (%s), expires at %s",
            _mask_token(token),
            data.get("expires_at", "unknown"),
        )
        return token

    def start_background_refresh(self) -> None:
        """Start a daemon thread that periodically refreshes the token and updates os.environ."""
        if self._refresh_thread_started:
            return
        self._refresh_thread_started = True
        thread = threading.Thread(target=self._background_refresh_loop, daemon=True)
        thread.start()
        logger.info("Started GitHub App token background refresh thread")

    def _background_refresh_loop(self) -> None:
        """Periodically refresh the token and update os.environ."""
        while True:
            logger.debug(
                "GitHub App token refresh thread sleeping for %ds",
                TOKEN_REFRESH_INTERVAL_SECONDS,
            )
            time.sleep(TOKEN_REFRESH_INTERVAL_SECONDS)

            try:
                token = self.refresh_token()
                os.environ["AUTO_GENERATED_GITHUB_TOKEN"] = token
            except Exception:
                logger.warning(
                    "Background refresh: failed to refresh GitHub App token",
                    exc_info=True,
                )


def ensure_github_app_token_env() -> None:
    """If GitHub App credentials are configured, generate an installation token,
    set it as AUTO_GENERATED_GITHUB_TOKEN in the environment, and start a
    background thread to keep it fresh.

    This should be called early in the application lifecycle, before
    environment variable substitution resolves MCP configs.
    """
    # Skip if GitHub App env vars are not configured
    if not GitHubAppTokenManager.has_github_app_env_vars():
        return

    # Don't override an existing token
    if os.environ.get("AUTO_GENERATED_GITHUB_TOKEN"):
        return

    manager = GitHubAppTokenManager.get_instance()
    if manager is None:
        return

    try:
        token = manager.refresh_token()
        os.environ["AUTO_GENERATED_GITHUB_TOKEN"] = token
        logger.debug(
            "Set AUTO_GENERATED_GITHUB_TOKEN (%s) from GitHub App installation token",
            _mask_token(token),
        )
        manager.start_background_refresh()
    except Exception:
        logger.warning(
            "Failed to generate GitHub App installation token", exc_info=True
        )
