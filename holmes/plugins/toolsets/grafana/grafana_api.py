import logging
import requests  # type: ignore
from typing import Tuple
import backoff

from holmes.plugins.toolsets.grafana.common import (
    GrafanaConfig,
    build_headers,
)


@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException,
    max_tries=2,
    giveup=lambda e: isinstance(e, requests.exceptions.HTTPError)
    and e.response.status_code < 500,
)
def _try_health_url(url: str, headers: dict) -> None:
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()


def grafana_health_check(config: GrafanaConfig) -> Tuple[bool, str]:
    """
    Tests a healthcheck url for grafna loki.
    1. When using grafana as proxy, grafana_datasource_uid is provided, use the data source health url (docs are added).
    2. When using loki directly there are two cases.
        a. Using loki cloud, health check is provided on the base url.
        b. Using local loki, uses url/healthcheck default is url/ready
        c. This function tries both direct loki cases for the user.
    """
    health_urls = []
    if config.grafana_datasource_uid:
        # https://grafana.com/docs/grafana/latest/developers/http_api/data_source/#check-data-source-health
        health_urls.append(
            f"{config.url}/api/datasources/uid/{config.grafana_datasource_uid}/health"
        )
    else:
        health_urls.append(f"{config.url}/{config.healthcheck}")
        health_urls.append(config.url)  # loki cloud uses no suffix.
    g_headers = build_headers(api_key=config.api_key, additional_headers=config.headers)

    error_msg = ""
    for url in health_urls:
        try:
            _try_health_url(url, g_headers)
            return True, ""
        except Exception as e:
            logging.debug(
                f"Failed to fetch grafana health status at {url}", exc_info=True
            )
            error_msg += f"Failed to fetch grafana health status at {url}. {str(e)}\n"

    # Add helpful hint if this looks like a common misconfiguration
    if config.grafana_datasource_uid and ":3100" in config.url:
        error_msg += (
            "\n\nPossible configuration issue: grafana_datasource_uid is set but URL contains port 3100 "
            "(typically used for direct Loki connections). Please verify:\n"
            "- If connecting directly to Loki: remove grafana_datasource_uid from config\n"
            "- If connecting via Grafana proxy: ensure URL points to Grafana (usually port 3000)"
        )

    return False, error_msg
