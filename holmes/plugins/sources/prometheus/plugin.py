import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Pattern

import humanize
import requests  # type: ignore
import rich
import rich.segment
from pydantic import parse_obj_as
from pydantic.json import pydantic_encoder
from requests.auth import HTTPBasicAuth  # type: ignore

from holmes.core.issue import Issue
from holmes.plugins.interfaces import SourcePlugin
from holmes.plugins.utils import dict_to_markdown

from .models import PrometheusAlert, PrometheusGettableAlert


class AlertManagerSource(SourcePlugin):
    """
    Issue IDs are of the format {alert_name}-{alert_fingerprint}-{starts_at} which is both unique and allows
    quickly identifying the alertname and using it to filter on issue_id
    """

    def __init__(
        self,
        url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        alertname_filter: Optional[Pattern] = None,
        label_filter: Optional[str] = None,
        filepath: Optional[Path] = None,
    ):
        super().__init__()
        self.url = url
        self.username = username
        self.password = password
        self.alertname_filter = alertname_filter
        self.label_filter = label_filter
        self.filepath = filepath

        if self.url is None and self.filepath is None:
            # we don't mention --alertmanager-file to avoid confusing users - most users wont care about it
            raise ValueError("--alertmanager-url must be specified")
        if self.url is not None and self.filepath is not None:
            logging.warning(
                "Ignoring --alertmanager-url because --alertmanager-file is specified"
            )
        if self.label_filter and self.filepath is not None:
            logging.warning(
                "Ignoring --label-filter because --alertmanager-file is specified"
            )
        if self.url and not (
            self.url.startswith("http://") or self.url.startswith("https://")
        ):
            raise ValueError("--alertmanager-url must start with http:// or https://")

    def __fetch_issues_from_api(self) -> List[PrometheusAlert]:
        fetch_alerts_url = f"{self.url}/api/v2/alerts"
        params = {
            "active": "true",
            "silenced": "false",
            "inhibited": "false",
        }
        if self.label_filter:
            params["filter"] = self.label_filter
            logging.info(f"Filtering alerts by {self.label_filter}")

        if self.username is not None or self.password is not None:
            auth = HTTPBasicAuth(self.username, self.password)  # type: ignore
        else:
            auth = None

        logging.info(f"Loading alerts from url {fetch_alerts_url}")
        response = requests.get(fetch_alerts_url, params=params, auth=auth)
        if response.status_code != 200:
            raise Exception(
                f"Failed to get live alerts: {response.status_code} {response.text}"
            )
        data = response.json()
        return [
            a.to_regular_prometheus_alert()
            for a in parse_obj_as(List[PrometheusGettableAlert], data)
        ]

    def __fetch_issues_from_file(self) -> List[PrometheusAlert]:
        logging.info(f"Loading alerts from file {self.filepath}")
        with open(self.filepath, "r") as f:  # type: ignore
            data = json.load(f)
        return parse_obj_as(List[PrometheusAlert], data)

    def fetch_issues(self) -> List[Issue]:
        if self.filepath is not None:
            alerts = self.__fetch_issues_from_file()
        else:
            alerts = self.__fetch_issues_from_api()

        if self.alertname_filter is not None:
            alertname_filter = re.compile(self.alertname_filter)
            alerts = [a for a in alerts if alertname_filter.match(a.unique_id)]

        return [
            Issue(
                id=alert.unique_id,
                name=alert.name,
                source_type="prometheus",
                source_instance_id=str(self.filepath) if self.filepath else self.url,  # type: ignore
                url=alert.generatorURL,
                presentation_key_metadata=f"*Severity*: {alert.labels['severity']}\n*Start Time*: {alert.startsAt.strftime('%Y-%m-%d %H:%M:%S UTC')}\n*Duration*: {humanize.naturaldelta(alert.duration)}",  # type: ignore
                presentation_all_metadata=self.__format_issue_metadata(alert),
                raw=alert.model_dump(),
            )
            for alert in alerts
        ]

    def dump_raw_alerts_to_file(self, path: Path) -> None:
        """
        Useful for generating test data
        """
        alerts = self.__fetch_issues_from_api()
        with open(path, "w") as f:
            f.write(json.dumps(alerts, default=pydantic_encoder, indent=2))

    def output_curl_commands(self, console: rich.console.Console) -> None:
        """
        Outputs curl commands to send each alert to Alertmanager via the API.
        """
        alerts = self.__fetch_issues_from_api()
        for alert in alerts:
            alert_json = json.dumps(
                [alert.model_dump()], default=pydantic_encoder
            )  # Wrap in a list
            curl_command = (
                f"curl -X POST -H 'Content-Type: application/json' "
                f"-d '{alert_json}' {self.url}/api/v2/alerts"
            )
            console.print(f"[green]{alert.name} alert[/green]")
            console.print(f"[yellow]{curl_command}[/yellow]", soft_wrap=True)

    @staticmethod
    def __format_issue_metadata(alert: PrometheusAlert) -> Optional[str]:
        if not alert.labels and not alert.annotations:
            return None
        text = ""
        if alert.labels:
            text += "*Labels:*\n"
            text += dict_to_markdown(alert.labels)
        if alert.annotations:
            text += "*Annotations:*\n"
            text += dict_to_markdown(alert.annotations)
        return text
