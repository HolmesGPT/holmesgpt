from typing import ClassVar, Dict, List, Optional

from pydantic import Field

from holmes.utils.pydantic_utils import ToolsetConfig

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


def get_base_url(config: GrafanaConfig) -> str:
    if config.grafana_datasource_uid:
        return f"{config.api_url}/api/datasources/proxy/uid/{config.grafana_datasource_uid}"
    else:
        return config.api_url


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
