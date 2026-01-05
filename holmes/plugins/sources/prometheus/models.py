import html
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel, computed_field


# these models are used by AlertManager's push API (when alertmanager pushes alerts to us by webhook)
# this is the standard format we use internally
class PrometheusAlert(BaseModel):
    status: str
    labels: Dict[str, str]
    annotations: Dict[str, str]
    startsAt: datetime
    endsAt: datetime
    generatorURL: Optional[str] = None
    fingerprint: str

    @computed_field  # type: ignore
    @property
    def unique_id(self) -> str:
        return f"{self.name}-{self.fingerprint}-{self.startsAt}"

    @computed_field  # type: ignore
    @property
    def duration(self) -> Union[timedelta, str]:
        if self.endsAt.year == 1:
            return "Ongoing"
        else:
            duration = self.endsAt - self.startsAt
            return duration

    @computed_field  # type: ignore
    @property
    def name(self) -> str:
        return self.labels["alertname"]

    @computed_field  # type: ignore
    @property
    def definition(self) -> str:
        """
        Returns the promql definition of this alert
        """
        url = self.generatorURL
        if not url:
            return ""

        # decode HTML entities to convert &#43; like representations to characters
        url = html.unescape(url)
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)

        q_expr = query_params.get("g0.expr", [])
        if len(q_expr) < 1 or not q_expr[0]:
            return ""

        return unquote(q_expr[0])


class PrometheusAlertGroup(BaseModel):
    receiver: str
    status: str
    alerts: List[PrometheusAlert]
    groupLabels: Dict[str, str]
    commonLabels: Dict[str, str]
    commonAnnotations: Dict[str, str]
    externalURL: str
    version: str
    groupKey: str
    truncatedAlerts: int


# these models are used by AlertManager's pull API (when pulling alerts from alertmanager via API)
class PrometheusReceiver(BaseModel):
    name: str


class PrometheusAlertStatus(BaseModel):
    state: str
    silencedBy: List[str]
    inhibitedBy: List[str]


class PrometheusGettableAlert(BaseModel):
    labels: Dict[str, str]
    generatorURL: Optional[str] = ""
    annotations: Dict[str, str]
    receivers: List[PrometheusReceiver]
    fingerprint: str
    startsAt: datetime
    updatedAt: datetime
    endsAt: datetime
    status: PrometheusAlertStatus

    def to_regular_prometheus_alert(self) -> PrometheusAlert:
        return PrometheusAlert(
            status=self.status.state,
            labels=self.labels,
            annotations=self.annotations,
            startsAt=self.startsAt,
            endsAt=self.endsAt,
            generatorURL=self.generatorURL,
            fingerprint=self.fingerprint,
        )
