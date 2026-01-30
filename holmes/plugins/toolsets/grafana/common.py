from typing import Dict, Optional

from pydantic import BaseModel, Field


class GrafanaConfig(BaseModel):
    """A config that represents one of the Grafana related tools like Loki or Tempo
    If `grafana_datasource_uid` is set, then it is assumed that Holmes will proxy all
    requests through grafana. In this case `url` should be the grafana URL.
    If `grafana_datasource_uid` is not set, it is assumed that the `url` is the
    systems' URL
    """

    url: str = Field(
        description="Grafana URL or direct datasource URL",
        examples=["YOUR GRAFANA URL", "http://grafana.monitoring.svc:3000"],
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Grafana API key for authentication",
        examples=["YOUR API KEY"],
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Additional HTTP headers to include in requests",
        examples=[{"Authorization": "Bearer YOUR_API_KEY"}],
    )
    grafana_datasource_uid: Optional[str] = Field(
        default=None,
        description="Grafana datasource UID to proxy requests through Grafana",
        examples=["loki", "tempo"],
    )
    external_url: Optional[str] = Field(
        default=None,
        description="External URL for linking to Grafana UI",
    )
    verify_ssl: bool = Field(
        default=True,
        description="Whether to verify SSL certificates",
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
        return f"{config.url}/api/datasources/proxy/uid/{config.grafana_datasource_uid}"
    else:
        return config.url


class GrafanaTempoLabelsConfig(BaseModel):
    pod: str = Field(default="k8s.pod.name", description="Label for pod name")
    namespace: str = Field(default="k8s.namespace.name", description="Label for namespace")
    deployment: str = Field(default="k8s.deployment.name", description="Label for deployment")
    node: str = Field(default="k8s.node.name", description="Label for node name")
    service: str = Field(default="service.name", description="Label for service name")


class GrafanaTempoConfig(GrafanaConfig):
    labels: GrafanaTempoLabelsConfig = Field(
        default_factory=GrafanaTempoLabelsConfig,
        description="Label mappings for Tempo spans",
    )
