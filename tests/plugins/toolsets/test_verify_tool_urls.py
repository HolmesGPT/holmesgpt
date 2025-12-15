from unittest.mock import MagicMock, patch
from urllib.parse import unquote, parse_qs, urlparse
import json
import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.grafana.common import (
    GrafanaTempoConfig,
    GrafanaConfig,
)
from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaDashboardConfig
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    GrafanaTempoToolset,
    FetchTracesSimpleComparison,
    SearchTracesByQuery,
    SearchTracesByTags,
    QueryTraceById,
    SearchTagNames,
    SearchTagValues,
    QueryMetricsInstant,
    QueryMetricsRange,
)
from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import (
    GrafanaLokiToolset,
    LokiQuery,
)
from holmes.plugins.toolsets.grafana.toolset_grafana import (
    GrafanaToolset,
    SearchDashboards,
    GetDashboardByUID,
    GetHomeDashboard,
    GetDashboardTags,
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
