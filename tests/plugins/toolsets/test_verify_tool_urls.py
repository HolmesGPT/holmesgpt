import base64
import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.coralogix.toolset_coralogix import (
    CoralogixToolset,
    ExecuteDataPrimeQuery,
)
from holmes.plugins.toolsets.coralogix.utils import CoralogixConfig
from holmes.plugins.toolsets.datadog.datadog_models import (
    DatadogGeneralConfig,
    DatadogMetricsConfig,
    DatadogTracesConfig,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_general import (
    DatadogAPIGet,
    DatadogAPIPostSearch,
    DatadogGeneralToolset,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_metrics import (
    DatadogMetricsToolset,
    ListActiveMetrics,
    ListMetricTags,
    QueryMetrics,
    QueryMetricsMetadata,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    AggregateSpans,
    DatadogTracesToolset,
    GetSpans,
)
from holmes.plugins.toolsets.grafana.common import (
    GrafanaConfig,
    GrafanaTempoConfig,
)
from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import (
    GrafanaLokiToolset,
    LokiQuery,
)
from holmes.plugins.toolsets.grafana.toolset_grafana import (
    GetDashboardByUID,
    GetDashboardTags,
    GetHomeDashboard,
    GrafanaDashboardConfig,
    GrafanaToolset,
    SearchDashboards,
)
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    FetchTracesSimpleComparison,
    GrafanaTempoToolset,
    QueryMetricsInstant,
    QueryMetricsRange,
    QueryTraceById,
    SearchTagNames,
    SearchTagValues,
    SearchTracesByQuery,
    SearchTracesByTags,
)
from holmes.plugins.toolsets.newrelic.newrelic import (
    ExecuteNRQLQuery,
    NewRelicToolset,
)
from tests.conftest import create_mock_tool_invoke_context


def get_mock_traces():
    return {
        "traces": [
            {
                "traceID": "test-trace-1",
                "rootServiceName": "test-service",
                "durationMs": 100,
                "startTimeUnixNano": "1609459200000000000",
            }
        ]
    }


def get_mock_trace_data():
    return {
        "batches": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "test-service"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "test-trace-1",
                                "spanId": "test-span",
                                "name": "GET /api/test",
                                "startTimeUnixNano": "1609459200000000000",
                                "endTimeUnixNano": "1609459200050000000",
                            }
                        ]
                    }
                ],
            }
        ]
    }


def get_mock_tag_names():
    return {"tagNames": ["service.name", "http.status_code", "db.operation"]}


def get_mock_tag_values():
    return {"tagValues": ["SELECT", "INSERT", "UPDATE"]}


def get_mock_metrics():
    return {
        "data": {
            "result": [
                {
                    "metric": {"service": "test-service"},
                    "value": [1609459200, "100"],
                }
            ]
        }
    }


def get_mock_logs():
    return [
        {
            "timestamp": "1609459200000000000",
            "log": "test log message",
            "labels": {"namespace": "default", "pod": "test-pod"},
        }
    ]


def get_mock_dashboards():
    return [
        {
            "uid": "test-dashboard-uid",
            "title": "Test Dashboard",
            "type": "dash-db",
        }
    ]


def get_mock_dashboard():
    return {
        "dashboard": {
            "uid": "test-dashboard-uid",
            "title": "Test Dashboard",
        },
        "meta": {},
    }


def get_mock_home_dashboard():
    return {
        "dashboard": {
            "uid": "home-dashboard-uid",
            "title": "Home Dashboard",
        }
    }


def get_mock_tags():
    return [
        {"term": "production", "count": 5},
        {"term": "monitoring", "count": 3},
    ]


BASE_URL = "http://localhost:3000"
EXTERNAL_URL = "http://grafana.example.com"
DATASOURCE_UID = "test-datasource-uid"


def url_panes_to_dict(url: str) -> dict:
    """Parse and decode the panes parameter from Explore URL."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    panes_encoded = query_params["panes"][0]
    panes_json = unquote(panes_encoded)
    return json.loads(panes_json)


class TestTempoURLs:
    BASE_URL = BASE_URL
    EXTERNAL_URL = EXTERNAL_URL
    DATASOURCE_UID = DATASOURCE_UID

    @staticmethod
    def setup_mocks():
        mock_api_patcher = patch(
            "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
        )
        mock_api_class = mock_api_patcher.start()
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api

        mock_api.search_traces_by_query.return_value = get_mock_traces()
        mock_api.search_traces_by_tags.return_value = get_mock_traces()
        mock_api.query_trace_by_id_v2.return_value = get_mock_trace_data()
        mock_api.search_tag_names_v2.return_value = get_mock_tag_names()
        mock_api.search_tag_values_v2.return_value = get_mock_tag_values()
        mock_api.query_metrics_instant.return_value = get_mock_metrics()
        mock_api.query_metrics_range.return_value = get_mock_metrics()

        return mock_api_patcher

    @pytest.fixture
    def config(self):
        return GrafanaTempoConfig(
            api_key="test-key",
            url=self.BASE_URL,
            external_url=self.EXTERNAL_URL,
            grafana_datasource_uid=self.DATASOURCE_UID,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = GrafanaTempoToolset()
        toolset._grafana_config = config
        return toolset

    TEST_CASES = [
        (
            FetchTracesSimpleComparison,
            {"service_name": "test-service", "start": "-3600", "end": "0"},
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
                and url_panes_to_dict(url)["tmp"]["datasource"] == cls.DATASOURCE_UID
            ),
        ),
        (
            SearchTracesByQuery,
            {
                "q": '{resource.service.name="test-service"}',
                "start": "-3600",
                "end": "0",
            },
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
                and url_panes_to_dict(url)["tmp"]["queries"][0]["queryType"]
                == "traceql"
            ),
        ),
        (
            SearchTracesByTags,
            {"tags": 'service.name="test-service"', "start": "-3600", "end": "0"},
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
            ),
        ),
        (
            QueryTraceById,
            {
                "trace_id": "777b09668888b773d51ffc8885ca5b",
                "start": "-3600",
                "end": "0",
            },
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
                and url_panes_to_dict(url)["tmp"]["queries"][0]["query"]
                == "777b09668888b773d51ffc8885ca5b"
            ),
        ),
        (
            SearchTagNames,
            {"start": "-3600", "end": "0"},
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
            ),
        ),
        (
            SearchTagValues,
            {"tag": "db.operation", "start": "-3600", "end": "0"},
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
                and url_panes_to_dict(url)["tmp"]["queries"][0]["queryType"]
                == "traceqlSearch"
                and "filters" in url_panes_to_dict(url)["tmp"]["queries"][0]
            ),
        ),
        (
            QueryMetricsInstant,
            {
                "q": '{resource.service.name="test"} | count()',
                "start": "-3600",
                "end": "0",
            },
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
            ),
        ),
        (
            QueryMetricsRange,
            {
                "q": '{resource.service.name="test"} | rate()',
                "start": "-3600",
                "end": "0",
            },
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
            ),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, tool_class, params, url_validator):
        tool = tool_class(toolset)
        mock_patcher = self.setup_mocks()

        try:
            context = create_mock_tool_invoke_context()
            result = tool.invoke(params=params, context=context)

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.url is not None
            assert url_validator(
                result.url, self
            ), f"URL validation failed for {tool_class.__name__}: {result.url}"
        finally:
            mock_patcher.stop()


class TestLokiURLs:
    BASE_URL = BASE_URL
    EXTERNAL_URL = EXTERNAL_URL
    DATASOURCE_UID = DATASOURCE_UID

    @staticmethod
    def setup_mocks():
        mock_patcher = patch(
            "holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki.execute_loki_query"
        )
        mock_patcher.start().return_value = get_mock_logs()
        return mock_patcher

    @pytest.fixture
    def config(self):
        return GrafanaConfig(
            api_key="test-key",
            url=self.BASE_URL,
            external_url=self.EXTERNAL_URL,
            grafana_datasource_uid=self.DATASOURCE_UID,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = GrafanaLokiToolset()
        toolset._grafana_config = config
        return toolset

    TEST_CASES = [
        (
            LokiQuery,
            {
                "query": '{namespace="default"}',
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-01T01:00:00Z",
            },
            lambda url, cls: (
                cls.EXTERNAL_URL in url
                and "/explore" in url
                and "schemaVersion=1" in url
                and url_panes_to_dict(url)["tmp"]["datasource"] == cls.DATASOURCE_UID
                and url_panes_to_dict(url)["tmp"]["queries"][0]["datasource"]["type"]
                == "loki"
            ),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, tool_class, params, url_validator):
        tool = tool_class(toolset=toolset)
        mock_patcher = self.setup_mocks()

        try:
            context = create_mock_tool_invoke_context()
            result = tool.invoke(params=params, context=context)

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.url is not None
            assert url_validator(
                result.url, self
            ), f"URL validation failed for {tool_class.__name__}: {result.url}"
        finally:
            mock_patcher.stop()


class TestDashboardURLs:
    BASE_URL = BASE_URL
    EXTERNAL_URL = EXTERNAL_URL

    @staticmethod
    def setup_mocks():
        def mock_make_request(endpoint, params, query_params=None, timeout=30):
            if "home" in endpoint:
                data = get_mock_home_dashboard()
            elif "tags" in endpoint:
                data = get_mock_tags()
            elif "uid" in endpoint:
                data = get_mock_dashboard()
            else:
                data = get_mock_dashboards()

            return MagicMock(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )

        mock_patcher = patch(
            "holmes.plugins.toolsets.grafana.toolset_grafana.BaseGrafanaTool._make_grafana_request"
        )
        mock_patcher.start().side_effect = mock_make_request
        return mock_patcher

    @pytest.fixture
    def config(self):
        return GrafanaDashboardConfig(
            api_key="test-key",
            url=self.BASE_URL,
            external_url=self.EXTERNAL_URL,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = GrafanaToolset()
        toolset._grafana_config = config
        return toolset

    TEST_CASES = [
        (
            SearchDashboards,
            {"query": "test", "tag": "production"},
            lambda url, cls: (cls.EXTERNAL_URL in url and "/dashboards" in url),
        ),
        (
            SearchDashboards,
            {"dashboardUIDs": "test-dashboard-uid"},
            lambda url, cls: (
                cls.EXTERNAL_URL in url and "/d/test-dashboard-uid" in url
            ),
        ),
        (
            GetDashboardByUID,
            {"uid": "test-dashboard-uid"},
            lambda url, cls: (
                cls.EXTERNAL_URL in url and "/d/test-dashboard-uid" in url
            ),
        ),
        (
            GetHomeDashboard,
            {},
            lambda url, cls: (
                cls.EXTERNAL_URL in url and "/d/home-dashboard-uid" in url
            ),
        ),
        (
            GetDashboardTags,
            {},
            lambda url, cls: (cls.EXTERNAL_URL in url and "/dashboards" in url),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, tool_class, params, url_validator):
        tool = tool_class(toolset)
        mock_patcher = self.setup_mocks()

        try:
            context = create_mock_tool_invoke_context()
            result = tool.invoke(params=params, context=context)

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.url is not None
            assert url_validator(
                result.url, self
            ), f"URL validation failed for {tool_class.__name__}: {result.url}"
        finally:
            mock_patcher.stop()


class TestCoralogixURLs:
    TEAM_HOSTNAME = "my-team"
    DOMAIN = "eu2.coralogix.com"
    BASE_URL = f"https://{TEAM_HOSTNAME}.{DOMAIN}"

    @staticmethod
    def extract_query_from_url(url: str) -> str:
        if "?" in url:
            query_part = url.split("?")[1]
            query_params = parse_qs(query_part)
            if "query" in query_params:
                return unquote(query_params["query"][0])
        return ""

    @staticmethod
    def setup_mocks():
        mock_patcher = patch(
            "holmes.plugins.toolsets.coralogix.toolset_coralogix.execute_dataprime_query"
        )
        mock_execute = mock_patcher.start()
        # Return empty list to simulate successful query with no results
        mock_execute.return_value = ([], None)
        return mock_patcher

    @pytest.fixture
    def config(self):
        return CoralogixConfig(
            api_key="test-key",
            team_hostname=self.TEAM_HOSTNAME,
            domain=self.DOMAIN,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = CoralogixToolset()
        toolset.config = config
        return toolset

    TEST_CASES = [
        (
            ExecuteDataPrimeQuery,
            {
                "query": "source logs | lucene 'error' | limit 100",
                "description": "test logs query",
                "query_type": "Logs",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-01T01:00:00Z",
            },
            lambda url, cls, params: (
                cls.BASE_URL in url
                and "/#/query-new/logs" in url
                and "querySyntax=dataprime" in url
                and "permalink=true" in url
                and "time=" in url
                and "query=" in url
                and cls.extract_query_from_url(url) == params["query"]
            ),
        ),
        (
            ExecuteDataPrimeQuery,
            {
                "query": "source spans | lucene 'my-service' | limit 100",
                "description": "test spans query",
                "query_type": "Traces",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-01T01:00:00Z",
            },
            lambda url, cls, params: (
                cls.BASE_URL in url
                and "/#/query-new/logs" in url
                and "querySyntax=dataprime" in url
                and "permalink=true" in url
                and cls.extract_query_from_url(url) == params["query"]
            ),
        ),
        (
            ExecuteDataPrimeQuery,
            {
                "query": "source logs | filter $m.severity == ERROR | limit 100",
                "description": "test archive query",
                "query_type": "Logs",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-01T01:00:00Z",
                "tier": "ARCHIVE",
            },
            lambda url, cls, params: (
                cls.BASE_URL in url
                and "/#/query-new/archive-logs" in url
                and "querySyntax=dataprime" in url
                and "permalink=true" in url
                and cls.extract_query_from_url(url) == params["query"]
            ),
        ),
        (
            ExecuteDataPrimeQuery,
            {
                "query": "source logs | limit 10",
                "description": "test frequent search",
                "query_type": "Logs",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-01T01:00:00Z",
                "tier": "FREQUENT_SEARCH",
            },
            lambda url, cls, params: (
                cls.BASE_URL in url
                and "/#/query-new/logs" in url
                and "querySyntax=dataprime" in url
                and "permalink=true" in url
                and cls.extract_query_from_url(url) == params["query"]
            ),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, tool_class, params, url_validator):
        tool = tool_class(toolset)
        mock_patcher = self.setup_mocks()

        try:
            context = create_mock_tool_invoke_context()
            result = tool.invoke(params=params, context=context)

            assert result.status in (
                StructuredToolResultStatus.SUCCESS,
                StructuredToolResultStatus.NO_DATA,
            )
            assert result.url is not None
            assert url_validator(
                result.url, self, params
            ), f"URL validation failed for {tool_class.__name__}: {result.url}"
        finally:
            mock_patcher.stop()


class TestNewRelicURLs:
    ACCOUNT_ID = "1234567"
    BASE_URL_US = "https://one.newrelic.com"
    BASE_URL_EU = "https://one.eu.newrelic.com"

    @staticmethod
    def extract_overlay_from_url(url: str) -> dict:
        """Extract and decode the overlay parameter from a New Relic URL."""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        if "overlay" in query_params:
            overlay_base64 = query_params["overlay"][0]
            overlay_json = base64.b64decode(overlay_base64).decode("utf-8")
            return json.loads(overlay_json)
        return {}

    @staticmethod
    def setup_mocks():
        mock_patcher = patch("holmes.plugins.toolsets.newrelic.newrelic.NewRelicAPI")
        mock_api_class = mock_patcher.start()
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.execute_nrql_query.return_value = []
        return mock_patcher

    @pytest.fixture
    def toolset_us(self):
        toolset = NewRelicToolset()
        toolset.nr_api_key = "test-key"
        toolset.nr_account_id = self.ACCOUNT_ID
        toolset.is_eu_datacenter = False
        return toolset

    @pytest.fixture
    def toolset_eu(self):
        toolset = NewRelicToolset()
        toolset.nr_api_key = "test-key"
        toolset.nr_account_id = self.ACCOUNT_ID
        toolset.is_eu_datacenter = True
        return toolset

    TEST_CASES = [
        (
            ExecuteNRQLQuery,
            {
                "query": "SELECT * FROM Transaction SINCE 1 hour ago",
                "description": "test transaction query",
                "query_type": "Traces",
            },
            False,
            lambda url, cls, params: (
                cls.BASE_URL_US in url
                and "/launcher/dashboards.launcher" in url
                and "pane=" in url
                and "overlay=" in url
                and cls.extract_overlay_from_url(url)
                .get("initialQueries", [{}])[0]
                .get("nrql")
                == params["query"]
                and cls.extract_overlay_from_url(url)
                .get("initialQueries", [{}])[0]
                .get("accountId")
                == int(cls.ACCOUNT_ID)
            ),
        ),
        (
            ExecuteNRQLQuery,
            {
                "query": "SELECT count(*) FROM Log SINCE 24 hours ago FACET loglevel",
                "description": "test logs query",
                "query_type": "Logs",
            },
            False,
            lambda url, cls, params: (
                cls.BASE_URL_US in url
                and "/launcher/dashboards.launcher" in url
                and "overlay=" in url
                and cls.extract_overlay_from_url(url)
                .get("initialQueries", [{}])[0]
                .get("nrql")
                == params["query"]
            ),
        ),
        (
            ExecuteNRQLQuery,
            {
                "query": "SELECT average(duration) FROM Transaction SINCE 1 day ago",
                "description": "test metrics query",
                "query_type": "Metrics",
            },
            True,
            lambda url, cls, params: (
                cls.BASE_URL_EU in url
                and "/launcher/dashboards.launcher" in url
                and "overlay=" in url
                and cls.extract_overlay_from_url(url)
                .get("initialQueries", [{}])[0]
                .get("nrql")
                == params["query"]
            ),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,is_eu,url_validator", TEST_CASES)
    def test_tool_urls(
        self, toolset_us, toolset_eu, tool_class, params, is_eu, url_validator
    ):
        toolset = toolset_eu if is_eu else toolset_us
        tool = tool_class(toolset)
        mock_patcher = self.setup_mocks()

        try:
            context = create_mock_tool_invoke_context()
            result = tool.invoke(params=params, context=context)

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.url is not None
            assert url_validator(
                result.url, self, params
            ), f"URL validation failed for {tool_class.__name__}: {result.url}"
        finally:
            mock_patcher.stop()


class TestDatadogMetricsURLs:
    BASE_URL = "https://api.datadoghq.com"
    APP_URL = "https://app.datadoghq.com"

    @pytest.fixture
    def mock_api(self):
        """Fixture to mock Datadog API calls."""
        with patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_metrics.execute_datadog_http_request"
        ) as mock_execute:
            mock_execute.return_value = {
                "metrics": ["system.cpu.user", "system.mem.used"],
                "series": [
                    {
                        "metric": "system.cpu.user",
                        "pointlist": [[1609459200000, 50.5]],
                        "scope": "host:test-host",
                    }
                ],
                "data": {
                    "type": "metric",
                    "id": "system.cpu.user",
                },
            }
            yield mock_execute

    @pytest.fixture
    def config(self):
        return DatadogMetricsConfig(
            dd_api_key="test-key",
            dd_app_key="test-app-key",
            site_api_url=self.BASE_URL,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = DatadogMetricsToolset()
        toolset.dd_config = config
        return toolset

    TEST_CASES = [
        (
            ListActiveMetrics,
            {"from_time": "-3600"},
            lambda url, cls: (cls.APP_URL in url and "/metric/summary" in url),
        ),
        (
            ListActiveMetrics,
            {"from_time": "-3600", "host": "test-host"},
            lambda url, cls: (
                cls.APP_URL in url
                and "/metric/summary" in url
                and "host=test-host" in url
            ),
        ),
        (
            QueryMetrics,
            {
                "query": "system.cpu.user{host:test-host}",
                "description": "CPU usage",
                "from_time": "-3600",
                "to_time": "0",
            },
            lambda url, cls: (
                cls.APP_URL in url and "/metric/explorer" in url and "query=" in url
            ),
        ),
        (
            QueryMetricsMetadata,
            {"metric_names": "system.cpu.user"},
            lambda url, cls: (
                cls.APP_URL in url
                and "/metric/summary" in url
                and "metric=system.cpu.user" in url
            ),
        ),
        (
            ListMetricTags,
            {"metric_name": "system.cpu.user"},
            lambda url, cls: (
                cls.APP_URL in url
                and "/metric/summary" in url
                and "metric=system.cpu.user" in url
            ),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, mock_api, tool_class, params, url_validator):
        tool = tool_class(toolset)
        context = create_mock_tool_invoke_context()
        result = tool.invoke(params=params, context=context)

        assert result.status in (
            StructuredToolResultStatus.SUCCESS,
            StructuredToolResultStatus.NO_DATA,
        )
        assert result.url is not None
        assert url_validator(
            result.url, self
        ), f"URL validation failed for {tool_class.__name__}: {result.url}"


class TestDatadogTracesURLs:
    BASE_URL = "https://api.datadoghq.com"
    APP_URL = "https://app.datadoghq.com"

    @pytest.fixture
    def mock_api(self):
        """Fixture to mock Datadog API calls."""
        with patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_traces.execute_datadog_http_request"
        ) as mock_execute:
            mock_execute.return_value = {
                "data": [
                    {
                        "id": "span-1",
                        "type": "span",
                        "attributes": {
                            "service": "test-service",
                            "resource_name": "GET /api/test",
                        },
                    }
                ],
                "meta": {"page": {"after": None}},
            }
            yield mock_execute

    @pytest.fixture
    def config(self):
        return DatadogTracesConfig(
            dd_api_key="test-key",
            dd_app_key="test-app-key",
            site_api_url=self.BASE_URL,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = DatadogTracesToolset()
        toolset.dd_config = config
        return toolset

    TEST_CASES = [
        (
            GetSpans,
            {
                "query": "service:test-service",
                "start_datetime": "-3600",
                "end_datetime": "0",
                "compact": True,
            },
            lambda url, cls: (
                cls.APP_URL in url and "/apm/traces" in url and "query=" in url
            ),
        ),
        (
            AggregateSpans,
            {
                "query": "service:test-service",
                "start_datetime": "-3600",
                "end_datetime": "0",
                "compute": [{"aggregation": "count", "type": "total"}],
            },
            lambda url, cls: (
                cls.APP_URL in url and "/apm/analytics" in url and "query=" in url
            ),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, mock_api, tool_class, params, url_validator):
        tool = tool_class(toolset)
        context = create_mock_tool_invoke_context()
        result = tool.invoke(params=params, context=context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.url is not None
        assert url_validator(
            result.url, self
        ), f"URL validation failed for {tool_class.__name__}: {result.url}"


class TestDatadogGeneralURLs:
    BASE_URL = "https://api.datadoghq.com"
    APP_URL = "https://app.datadoghq.com"

    @pytest.fixture
    def mock_api(self):
        """Fixture to mock Datadog API calls."""
        with patch(
            "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request"
        ) as mock_execute:
            mock_execute.return_value = {
                "monitors": [{"id": 12345, "name": "Test Monitor"}],
                "data": [{"id": "test-id", "type": "monitor"}],
            }
            yield mock_execute

    @pytest.fixture
    def config(self):
        return DatadogGeneralConfig(
            dd_api_key="test-key",
            dd_app_key="test-app-key",
            site_api_url=self.BASE_URL,
        )

    @pytest.fixture
    def toolset(self, config):
        toolset = DatadogGeneralToolset()
        toolset.dd_config = config
        return toolset

    TEST_CASES = [
        (
            DatadogAPIGet,
            {
                "endpoint": "/api/v1/monitor",
                "description": "List monitors",
                "query_params": {},
            },
            lambda url, cls: (cls.APP_URL in url and "/monitors" in url),
        ),
        (
            DatadogAPIGet,
            {
                "endpoint": "/api/v1/monitor/12345",
                "description": "Get monitor",
                "query_params": {},
            },
            lambda url, cls: (cls.APP_URL in url and "/monitors/12345" in url),
        ),
        (
            DatadogAPIGet,
            {
                "endpoint": "/api/v1/events",
                "description": "Get events",
                "query_params": {"start": 1609459200, "end": 1609545600},
            },
            lambda url, cls: (
                cls.APP_URL in url and "/events" in url and "from_ts" in url
            ),
        ),
        (
            DatadogAPIPostSearch,
            {
                "endpoint": "/api/v2/monitor/search",
                "description": "Search monitors",
                "body": {"query": "env:production", "page": 0, "per_page": 20},
            },
            lambda url, cls: (cls.APP_URL in url and "/monitors" in url),
        ),
        (
            DatadogAPIPostSearch,
            {
                "endpoint": "/api/v2/logs/events/search",
                "description": "Search logs",
                "body": {
                    "filter": {
                        "from": "2024-01-01T00:00:00Z",
                        "to": "2024-01-02T00:00:00Z",
                        "query": "*",
                    },
                    "page": {"limit": 50},
                },
            },
            lambda url, cls: (cls.APP_URL in url and "/logs" in url),
        ),
    ]

    @pytest.mark.parametrize("tool_class,params,url_validator", TEST_CASES)
    def test_tool_urls(self, toolset, mock_api, tool_class, params, url_validator):
        tool = tool_class(toolset)
        context = create_mock_tool_invoke_context()
        result = tool.invoke(params=params, context=context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.url is not None
        assert url_validator(
            result.url, self
        ), f"URL validation failed for {tool_class.__name__}: {result.url}"
