import logging
from typing import Any, ClassVar, Dict, List, Optional, Type, Union

from pydantic import Field, ValidationError, model_validator
from requests.auth import HTTPBasicAuth

from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

GRAFANA_ICON_URL = "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/grafana.svg"
LOKI_ICON_URL = "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/grafana.svg"
TEMPO_ICON_URL = "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/grafana.svg"


class GrafanaConfig(ToolsetConfig):
    """A config that represents one of the Grafana related tools like Loki or Tempo
    If `grafana_datasource_uid` is set, then it is assumed that Holmes will proxy all
    requests through grafana. In this case `api_url` should be the grafana URL.
    If `grafana_datasource_uid` is not set, it is assumed that the `api_url` is the
    systems' URL
    """

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "url": "api_url",
        "headers": "additional_headers",
    }

    api_url: str = Field(
        title="URL",
        description="Grafana URL or direct datasource URL",
        examples=["YOUR GRAFANA URL", "http://grafana.monitoring.svc:3000"],
    )
    api_key: Optional[str] = Field(
        default=None,
        title="API Key",
        description="Grafana API key for authentication",
        examples=["YOUR API KEY"],
    )
    additional_headers: Optional[Dict[str, str]] = Field(
        default=None,
        title="Additional Headers",
        description="Additional HTTP headers to include in requests",
        examples=[{"Authorization": "Bearer YOUR_API_KEY"}],
    )
    grafana_datasource_uid: Optional[str] = Field(
        default=None,
        title="Datasource UID",
        description="Grafana datasource UID to proxy requests through Grafana",
        examples=["loki", "tempo"],
    )
    external_url: Optional[str] = Field(
        default=None,
        title="External URL",
        description="External URL for linking to Grafana UI",
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates",
    )
    timeout_seconds: int = Field(
        default=30,
        gt=0,
        title="Timeout Seconds",
        description="Request timeout in seconds for Grafana API calls",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        title="Max Retries",
        description="Maximum number of retry attempts for failed Grafana API requests",
    )


def build_headers(api_key: Optional[str], additional_headers: Optional[Dict[str, str]]):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if additional_headers:
        headers.update(additional_headers)

    return headers


def get_base_url(target: Union["GrafanaConfig", "GrafanaInstance"]) -> str:
    if target.grafana_datasource_uid:
        return f"{target.api_url}/api/datasources/proxy/uid/{target.grafana_datasource_uid}"
    else:
        return target.api_url


class GrafanaLokiProxyConfig(GrafanaConfig):
    """Self-hosted Loki accessed via a self-hosted Grafana datasource proxy."""

    _name: ClassVar[Optional[str]] = "Self-Hosted Loki via Grafana Proxy"
    _description: ClassVar[Optional[str]] = (
        "Route queries through your Grafana's Loki datasource proxy."
    )
    _icon_url: ClassVar[Optional[str]] = GRAFANA_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "self-hosted-loki-via-grafana-proxy"
    _hidden_fields: ClassVar[List[str]] = ["additional_headers"]
    _recommended: ClassVar[bool] = True

    api_url: str = Field(  # type: ignore[assignment]
        title="Grafana URL",
        description="Base URL of your Grafana instance",
        examples=["http://robusta-grafana.default.svc.cluster.local"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="API Key",
        description="Grafana service account token with Viewer role",
        examples=["{{ env.GRAFANA_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )
    grafana_datasource_uid: str = Field(  # type: ignore[assignment]
        title="Loki Datasource UID",
        description="UID of the Loki datasource configured in Grafana",
        examples=["loki"],
    )


class DirectLokiConfig(GrafanaConfig):
    """Direct connection to a self-hosted Loki API endpoint without Grafana."""

    _name: ClassVar[Optional[str]] = "Self-Hosted Loki - Direct Connection"
    _description: ClassVar[Optional[str]] = (
        "Query your Loki API directly, without going through Grafana."
    )
    _icon_url: ClassVar[Optional[str]] = LOKI_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "self-hosted-loki-direct-connection"
    _hidden_fields: ClassVar[List[str]] = [
        "api_key",
        "grafana_datasource_uid",
        "external_url",
    ]

    api_url: str = Field(  # type: ignore[assignment]
        title="Loki URL",
        description="Base URL of your Loki server",
        examples=["http://loki.monitoring.svc.cluster.local:3100"],
    )
    additional_headers: Dict[str, str] = Field(
        default_factory=dict,
        title="Additional Headers",
        description=(
            "Optional HTTP headers to include in requests. "
            "For multi-tenant Loki, set `X-Scope-OrgID` to your tenant ID."
        ),
        examples=[{"X-Scope-OrgID": "<tenant id>"}],
    )


class GrafanaCloudLokiConfig(GrafanaConfig):
    """Grafana Cloud Loki accessed via your Grafana Cloud Grafana datasource proxy."""

    _name: ClassVar[Optional[str]] = "Grafana Cloud"
    _description: ClassVar[Optional[str]] = (
        "Route queries through your Grafana Cloud Grafana's Loki datasource proxy."
    )
    _icon_url: ClassVar[Optional[str]] = GRAFANA_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "grafana-cloud"
    _hidden_fields: ClassVar[List[str]] = ["additional_headers"]

    api_url: str = Field(  # type: ignore[assignment]
        title="Grafana Cloud URL",
        description="URL of your Grafana Cloud Grafana instance",
        examples=["https://<your-stack>.grafana.net"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="API Key",
        description="Grafana Cloud service account token with Viewer role",
        examples=["{{ env.GRAFANA_CLOUD_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )
    grafana_datasource_uid: str = Field(  # type: ignore[assignment]
        title="Loki Datasource UID",
        description="UID of the Loki datasource configured in your Grafana Cloud Grafana",
        examples=["grafanacloud-logs"],
    )


class GrafanaTempoLabelsConfig(ToolsetConfig):
    pod: str = Field(
        default="k8s.pod.name", title="Pod Label", description="Label for pod name"
    )
    namespace: str = Field(
        default="k8s.namespace.name",
        title="Namespace Label",
        description="Label for namespace",
    )
    deployment: str = Field(
        default="k8s.deployment.name",
        title="Deployment Label",
        description="Label for deployment",
    )
    node: str = Field(
        default="k8s.node.name", title="Node Label", description="Label for node name"
    )
    service: str = Field(
        default="service.name",
        title="Service Label",
        description="Label for service name",
    )


class GrafanaTempoConfig(GrafanaConfig):
    labels: GrafanaTempoLabelsConfig = Field(
        default_factory=GrafanaTempoLabelsConfig,
        title="Labels",
        description="Label mappings for Tempo spans",
    )


class GrafanaTempoProxyConfig(GrafanaTempoConfig):
    """Self-hosted Tempo accessed via a self-hosted Grafana datasource proxy."""

    _name: ClassVar[Optional[str]] = "Self-Hosted Tempo via Grafana Proxy"
    _description: ClassVar[Optional[str]] = (
        "Route queries through your Grafana's Tempo datasource proxy."
    )
    _icon_url: ClassVar[Optional[str]] = GRAFANA_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "self-hosted-tempo-via-grafana-proxy"
    _hidden_fields: ClassVar[List[str]] = ["additional_headers"]
    _recommended: ClassVar[bool] = True

    api_url: str = Field(  # type: ignore[assignment]
        title="Grafana URL",
        description="Base URL of your Grafana instance",
        examples=["http://robusta-grafana.default.svc.cluster.local"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="API Key",
        description="Grafana service account token with Viewer role and Data sources -> Reader permission",
        examples=["{{ env.GRAFANA_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )
    grafana_datasource_uid: str = Field(  # type: ignore[assignment]
        title="Tempo Datasource UID",
        description="UID of the Tempo datasource configured in Grafana",
        examples=["tempo"],
    )


class DirectTempoConfig(GrafanaTempoConfig):
    """Direct connection to a self-hosted Tempo API endpoint without Grafana."""

    _name: ClassVar[Optional[str]] = "Self-Hosted Tempo - Direct Connection"
    _description: ClassVar[Optional[str]] = (
        "Query your Tempo API directly, without going through Grafana."
    )
    _icon_url: ClassVar[Optional[str]] = TEMPO_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "self-hosted-tempo-direct-connection"
    _hidden_fields: ClassVar[List[str]] = [
        "api_key",
        "grafana_datasource_uid",
        "external_url",
    ]

    api_url: str = Field(  # type: ignore[assignment]
        title="Tempo URL",
        description="Base URL of your Tempo server (Tempo's HTTP API listens on 3200 by default)",
        examples=["http://tempo.monitoring.svc.cluster.local:3200"],
    )
    additional_headers: Dict[str, str] = Field(
        default_factory=dict,
        title="Additional Headers",
        description=(
            "Optional HTTP headers to include in requests. "
            "For multi-tenant Tempo, set `X-Scope-OrgID` to your tenant ID."
        ),
        examples=[{"X-Scope-OrgID": "<tenant id>"}],
    )


class GrafanaCloudTempoConfig(GrafanaTempoConfig):
    """Grafana Cloud Tempo accessed via your Grafana Cloud Grafana datasource proxy."""

    _name: ClassVar[Optional[str]] = "Grafana Cloud"
    _description: ClassVar[Optional[str]] = (
        "Route queries through your Grafana Cloud Grafana's Tempo datasource proxy."
    )
    _icon_url: ClassVar[Optional[str]] = GRAFANA_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "grafana-cloud"
    _hidden_fields: ClassVar[List[str]] = ["additional_headers"]

    api_url: str = Field(  # type: ignore[assignment]
        title="Grafana Cloud URL",
        description="URL of your Grafana Cloud Grafana instance",
        examples=["https://<your-stack>.grafana.net"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="API Key",
        description="Grafana Cloud service account token with Viewer role and Data sources -> Reader permission",
        examples=["{{ env.GRAFANA_CLOUD_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )
    grafana_datasource_uid: str = Field(  # type: ignore[assignment]
        title="Tempo Datasource UID",
        description="UID of the Tempo datasource configured in your Grafana Cloud Grafana",
        examples=["grafanacloud-traces"],
    )


# --- Multi-instance support (dashboards-only) ---


class GrafanaInstance(ToolsetConfig):
    """Connection details for a single Grafana target in a multi-instance setup."""

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "url": "api_url",
        "headers": "additional_headers",
    }

    name: str = Field(
        title="Name",
        description="Stable identifier the LLM uses to select this instance",
        examples=["prod-eu"],
    )
    api_url: str = Field(title="URL")
    api_key: Optional[str] = Field(default=None, json_schema_extra={"format": "password"})
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, json_schema_extra={"format": "password"})
    additional_headers: Optional[Dict[str, str]] = None
    grafana_datasource_uid: Optional[str] = None
    external_url: Optional[str] = None
    # `None` on the per-instance level means "inherit from the top-level global".
    verify_ssl: Optional[bool] = None
    timeout_seconds: Optional[int] = Field(default=None, gt=0)
    max_retries: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_auth(self) -> "GrafanaInstance":
        if self.api_key and (self.username or self.password):
            raise ValueError(
                f"Grafana instance '{self.name}': use `api_key` OR `username` + `password`, not both"
            )
        if bool(self.username) != bool(self.password):
            raise ValueError(
                f"Grafana instance '{self.name}': `username` and `password` must be set together"
            )
        return self


def build_auth(instance: GrafanaInstance) -> Optional[HTTPBasicAuth]:
    if instance.username and instance.password:
        return HTTPBasicAuth(instance.username, instance.password)
    return None


class MultiInstanceGrafanaConfig(GrafanaConfig):
    """Grafana config that accepts an `instances` list for multi-target routing.

    If `instances` is set, each entry is a `GrafanaInstance` and the top-level
    connection fields act as global defaults inherited by any instance that
    doesn't override them.

    If `instances` is unset, the top-level fields synthesize a single instance
    named `"default"` (the legacy single-instance shape).
    """

    # Per-toolset subclasses can register `GrafanaInstance` variants their
    # entries can match. The matcher tries each in order and picks the first
    # one that validates.
    instance_classes: ClassVar[List[Type[GrafanaInstance]]] = [GrafanaInstance]

    api_url: Optional[str] = None  # type: ignore[assignment]
    api_key: Optional[str] = Field(default=None, json_schema_extra={"format": "password"})
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, json_schema_extra={"format": "password"})

    instances: Optional[List[GrafanaInstance]] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_instances_against_variants(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("instances")
        if not raw:
            return data
        variants = cls.instance_classes or [GrafanaInstance]
        coerced: List[GrafanaInstance] = []
        for idx, item in enumerate(raw):
            if isinstance(item, GrafanaInstance):
                coerced.append(item)
                continue
            if not isinstance(item, dict):
                raise ValueError(
                    f"`instances[{idx}]` must be a dict, got {type(item).__name__}"
                )
            last_err: Optional[Exception] = None
            for variant in variants:
                try:
                    coerced.append(variant(**item))
                    break
                except ValidationError as e:
                    last_err = e
                    continue
            else:
                raise ValueError(
                    f"Grafana instance '{item.get('name', idx)}' did not match any variant "
                    f"({[v.__name__ for v in variants]}). Last error: {last_err}"
                )
        data["instances"] = coerced
        return data

    @model_validator(mode="after")
    def _normalize_and_resolve_globals(self) -> "MultiInstanceGrafanaConfig":
        # Top-level auth must follow the same XOR rule as per-instance auth so
        # mixed credentials are rejected up front rather than silently letting
        # `api_key` win over `username`/`password` in the fall-through below.
        if self.api_key and (self.username or self.password):
            raise ValueError(
                "Grafana config: use top-level `api_key` OR `username` + `password`, not both"
            )
        if bool(self.username) != bool(self.password):
            raise ValueError(
                "Grafana config: top-level `username` and `password` must be set together"
            )

        if not self.instances:
            if not self.api_url:
                raise ValueError(
                    "Either `instances` or top-level `api_url` is required for the Grafana toolset"
                )
            instance_cls = (self.instance_classes or [GrafanaInstance])[0]
            self.instances = [
                instance_cls(
                    name="default",
                    api_url=self.api_url,
                    api_key=self.api_key,
                    username=self.username,
                    password=self.password,
                )
            ]
        elif self.api_url:
            logger.warning(
                "MultiInstanceGrafanaConfig: top-level `api_url` is ignored when `instances` is set. "
                "Move connection fields into an entry under `instances`."
            )

        seen: set[str] = set()
        for inst in self.instances:
            if inst.name in seen:
                raise ValueError(f"Duplicate Grafana instance name: '{inst.name}'")
            seen.add(inst.name)
            if inst.verify_ssl is None:
                inst.verify_ssl = self.verify_ssl
            if inst.timeout_seconds is None:
                inst.timeout_seconds = self.timeout_seconds
            if inst.max_retries is None:
                inst.max_retries = self.max_retries
            if inst.additional_headers is None and self.additional_headers is not None:
                inst.additional_headers = self.additional_headers
            if inst.grafana_datasource_uid is None and self.grafana_datasource_uid:
                inst.grafana_datasource_uid = self.grafana_datasource_uid
            if inst.external_url is None and self.external_url:
                inst.external_url = self.external_url
            # Auth-only fall-through: instances without their own auth inherit
            # the global credentials.
            if not (inst.api_key or inst.username or inst.password):
                if self.api_key:
                    inst.api_key = self.api_key
                elif self.username and self.password:
                    inst.username = self.username
                    inst.password = self.password
                inst.validate_auth()
        return self
