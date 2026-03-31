import json
import logging
from typing import Any, Dict, Optional

import boto3
import requests
from pycognito import Cognito
from pydantic import Field, model_validator

from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


def _fetch_secret(secret_arn: str) -> Dict[str, str]:
    """Fetch a JSON secret from AWS Secrets Manager.

    Args:
        secret_arn: Full ARN of the secret.

    Returns:
        Parsed JSON dict with secret values.
    """
    # Extract region from ARN: arn:aws:secretsmanager:<region>:<account>:secret:<name>
    arn_parts = secret_arn.split(":")
    region = arn_parts[3] if len(arn_parts) > 3 else "us-east-1"

    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


class DBADashConfig(ToolsetConfig):
    """Configuration for the DBADash Web toolset.

    Supports AWS Cognito authentication. The client authenticates via
    Cognito SRP to obtain an idToken, then exchanges it with dbdash-web
    for a session cookie.

    Credentials can be provided directly or via an AWS Secrets Manager ARN.
    When secrets_manager_arn is set, username, password, cognito_user_pool_id,
    and cognito_client_id are fetched from the secret at startup.
    """

    api_url: str = Field(
        title="DBADash Web URL",
        description="Base URL of the dbdash-web instance",
        examples=["https://db-monitor.shared.platform.pditechnologies.com"],
    )
    secrets_manager_arn: Optional[str] = Field(
        default=None,
        title="Secrets Manager ARN",
        description=(
            "AWS Secrets Manager ARN containing credentials JSON with keys: "
            "username, password, cognito_user_pool_id, cognito_client_id. "
            "When set, these fields are fetched from the secret and override "
            "any values provided directly in config."
        ),
        examples=["arn:aws:secretsmanager:us-east-1:827852520868:secret:holmesgpt/dbdash-web/credentials-ZlBuJ0"],
    )
    username: Optional[str] = Field(
        default=None,
        title="Username",
        description="Cognito username. Not required if secrets_manager_arn is set.",
        examples=["pdi-integrations@pditechnologies.com"],
    )
    password: Optional[str] = Field(
        default=None,
        title="Password",
        description="Cognito password. Not required if secrets_manager_arn is set.",
        examples=["{{ env.DBDASH_PASSWORD }}"],
    )
    cognito_user_pool_id: Optional[str] = Field(
        default=None,
        title="Cognito User Pool ID",
        description="AWS Cognito User Pool ID. If not set, fetched from secret or auto-detected from dbdash-web.",
        examples=["us-east-1_9gRg2AHTs"],
    )
    cognito_client_id: Optional[str] = Field(
        default=None,
        title="Cognito Client ID",
        description="AWS Cognito App Client ID. If not set, fetched from secret or auto-detected from dbdash-web.",
        examples=["1clgfn33uiu5culblqrsdsfo8d"],
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

    @model_validator(mode="after")
    def resolve_secrets(self) -> "DBADashConfig":
        """Fetch credentials from Secrets Manager if ARN is provided."""
        if not self.secrets_manager_arn:
            # Validate that credentials are provided directly
            if not self.username or not self.password:
                raise ValueError(
                    "Either secrets_manager_arn or both username and password must be provided."
                )
            return self

        logger.debug("Fetching dbdash credentials from Secrets Manager: %s", self.secrets_manager_arn)
        secret = _fetch_secret(self.secrets_manager_arn)

        # Override fields from secret (secret values take precedence)
        self.username = secret.get("username", self.username)
        self.password = secret.get("password", self.password)
        self.cognito_user_pool_id = secret.get("cognito_user_pool_id", self.cognito_user_pool_id)
        self.cognito_client_id = secret.get("cognito_client_id", self.cognito_client_id)

        if not self.username or not self.password:
            raise ValueError(
                f"Secret {self.secrets_manager_arn} must contain 'username' and 'password' keys."
            )

        return self


def filter_instances_by_tags(
    instances: list[Dict[str, Any]],
    instance_tags: list[Dict[str, Any]],
    configured_tags: Optional[Dict[str, str]],
) -> list[Dict[str, Any]]:
    """Filter instances to only those matching ALL configured tags.

    Args:
        instances: List of {"InstanceID": int, "InstanceDisplayName": str}
        instance_tags: List of {"InstanceID": int, "TagName": str, "TagValue": str}
        configured_tags: Tags to filter by (e.g., {"project": "payments"}).
                         If None, returns all instances.

    Returns:
        Filtered list of instances matching all configured tags.
    """
    if not configured_tags:
        return instances

    # Build a map: instance_id -> {tag_name: tag_value}
    tag_map: Dict[int, Dict[str, str]] = {}
    for tag_entry in instance_tags:
        instance_id = tag_entry["InstanceID"]
        if instance_id not in tag_map:
            tag_map[instance_id] = {}
        tag_map[instance_id][tag_entry["TagName"]] = tag_entry["TagValue"]

    # Filter instances where ALL configured tags match
    filtered = []
    for instance in instances:
        instance_id = instance["InstanceID"]
        instance_tag_values = tag_map.get(instance_id, {})
        if all(
            instance_tag_values.get(tag_name) == tag_value
            for tag_name, tag_value in configured_tags.items()
        ):
            filtered.append(instance)

    return filtered


class DBADashClient:
    """Thin HTTP wrapper for dbdash-web API with Cognito SRP authentication.

    Authentication flow:
    1. Fetch Cognito config from /api/auth/config (if not provided)
    2. Authenticate with Cognito via SRP to get an idToken
    3. Exchange idToken with dbdash-web /api/auth/login for session cookies
    4. Use session cookies for all subsequent API calls
    5. On 401, re-authenticate and retry once
    """

    def __init__(self, config: DBADashConfig):
        self._config = config
        self._session: Optional[requests.Session] = None
        self._authenticated = False
        self._cognito_user_pool_id: Optional[str] = config.cognito_user_pool_id
        self._cognito_client_id: Optional[str] = config.cognito_client_id

    def _ensure_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = self._config.verify_ssl
        return self._session

    def _fetch_cognito_config(self) -> None:
        """Fetch Cognito User Pool ID and Client ID from dbdash-web."""
        if self._cognito_user_pool_id and self._cognito_client_id:
            return

        logger.debug("Fetching Cognito config from %s/api/auth/config", self._config.api_url)
        response = requests.get(
            f"{self._config.api_url}/api/auth/config",
            timeout=self._config.timeout_seconds,
            verify=self._config.verify_ssl,
        )
        response.raise_for_status()
        auth_config = response.json()

        self._cognito_user_pool_id = auth_config.get("userPoolId")
        self._cognito_client_id = auth_config.get("clientId")

        if not self._cognito_user_pool_id or not self._cognito_client_id:
            raise ValueError(
                f"Failed to get Cognito config from {self._config.api_url}/api/auth/config. "
                f"Response: {auth_config}. "
                "Set cognito_user_pool_id and cognito_client_id in config manually."
            )
        logger.debug(
            "Got Cognito config: pool=%s, client=%s",
            self._cognito_user_pool_id,
            self._cognito_client_id,
        )

    def _get_cognito_tokens(self) -> Dict[str, str]:
        """Authenticate with Cognito via SRP and return idToken + refreshToken."""
        self._fetch_cognito_config()

        cognito_user = Cognito(
            self._cognito_user_pool_id,
            self._cognito_client_id,
            username=self._config.username,
        )
        cognito_user.authenticate(password=self._config.password)

        return {
            "idToken": cognito_user.id_token,
            "refreshToken": cognito_user.refresh_token,
        }

    def _login(self) -> None:
        """Authenticate via Cognito SRP and exchange token with dbdash-web."""
        tokens = self._get_cognito_tokens()

        session = self._ensure_session()
        response = session.post(
            f"{self._config.api_url}/api/auth/login",
            json=tokens,
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        self._authenticated = True
        logger.debug("Successfully authenticated with dbdash-web via Cognito")

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self._login()

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Make an authenticated GET request to dbdash-web.

        Automatically logs in on first call and retries once on 401.

        Args:
            endpoint: API path (e.g., "/api/instances")
            params: Optional query parameters

        Returns:
            Parsed JSON response
        """
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
        """Login and call /api/health to verify connectivity."""
        return self.get("/api/health")
