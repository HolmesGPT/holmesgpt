import json
from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.coralogix.api import (
    CoralogixTier,
    execute_dataprime_query,
)
from holmes.plugins.toolsets.coralogix.toolset_coralogix import (
    CoralogixToolset,
    ExecuteDataPrimeQuery,
)
from holmes.plugins.toolsets.coralogix.utils import (
    CoralogixConfig,
    get_ui_base_url,
    normalize_datetime,
)


@pytest.fixture
def coralogix_config():
    return CoralogixConfig(
        api_key="dummy_api_key",
        team_slug="my-team",
        domain="eu2.coralogix.com",
    )


@pytest.fixture
def coralogix_toolset(coralogix_config):
    toolset = CoralogixToolset()
    toolset.config = coralogix_config
    return toolset


@pytest.mark.parametrize(
    "input_date,expected_output",
    [
        ("", "UNKNOWN_TIMESTAMP"),
        (None, "UNKNOWN_TIMESTAMP"),
        ("not a date", "not a date"),
        ("2023-01-01T12:30:45", "2023-01-01T12:30:45.000000Z"),
        ("2023-01-01T12:30:45.123456Z", "2023-01-01T12:30:45.123456Z"),
    ],
)
def test_normalize_datetime(input_date, expected_output):
    assert normalize_datetime(input_date) == expected_output


class TestUIPermalinkBaseURL:
    """Tests for get_ui_base_url (ROB-1395).

    The Coralogix team UI hostname differs from the API domain in most regions
    (e.g. US2 API domain is us2.coralogix.com / cx498.coralogix.com but the UI
    lives at <team>.app.cx498.coralogix.com), so permalinks must not reuse the
    API domain verbatim.
    """

    @pytest.mark.parametrize(
        "domain,expected_host",
        [
            # US1 (Ohio)
            ("us1.coralogix.com", "app.coralogix.us"),
            ("coralogix.us", "app.coralogix.us"),
            # US2 (Oregon) - the originally reported bug
            ("us2.coralogix.com", "app.cx498.coralogix.com"),
            ("cx498.coralogix.com", "app.cx498.coralogix.com"),
            # US3 (Iowa)
            ("us3.coralogix.com", "app.us3.coralogix.com"),
            # EU1 (Ireland) - only region whose team hostname has no 'app.' prefix
            ("eu1.coralogix.com", "coralogix.com"),
            ("coralogix.com", "coralogix.com"),
            # EU2 (Stockholm)
            ("eu2.coralogix.com", "app.eu2.coralogix.com"),
            # AP1 (Mumbai)
            ("ap1.coralogix.com", "app.coralogix.in"),
            ("coralogix.in", "app.coralogix.in"),
            # AP2 (Singapore)
            ("ap2.coralogix.com", "app.coralogixsg.com"),
            ("coralogixsg.com", "app.coralogixsg.com"),
            # AP3 (Jakarta)
            ("ap3.coralogix.com", "app.ap3.coralogix.com"),
            # GOV1 (AWS GovCloud, FedRAMP)
            ("gov1.coralogixgov.us", "app.gov1.coralogixgov.us"),
        ],
    )
    def test_maps_api_domain_to_team_ui_hostname(self, domain, expected_host):
        """Each documented Coralogix domain maps to its official team UI hostname."""
        config = CoralogixConfig(api_key="k", team_slug="acme", domain=domain)
        assert get_ui_base_url(config) == f"https://acme.{expected_host}"

    @pytest.mark.parametrize(
        "domain",
        [
            "US2.Coralogix.com",  # case-insensitive
            " us2.coralogix.com ",  # surrounding whitespace
            "us2.coralogix.com/",  # trailing slash
            "us2.coralogix.com.",  # trailing dot (FQDN form)
            "https://us2.coralogix.com",  # scheme pasted in by mistake
        ],
    )
    def test_domain_is_normalized_before_mapping(self, domain):
        """Domain casing/whitespace/scheme/trailing chars are normalized before lookup."""
        config = CoralogixConfig(api_key="k", team_slug="acme", domain=domain)
        assert get_ui_base_url(config) == "https://acme.app.cx498.coralogix.com"

    def test_unknown_non_coralogix_domain_falls_back_to_domain_itself(self):
        """Non-Coralogix (custom) domains keep the {team_slug}.{domain} behavior."""
        config = CoralogixConfig(
            api_key="k", team_slug="acme", domain="logs.my-company.internal"
        )
        assert get_ui_base_url(config) == "https://acme.logs.my-company.internal"

    @pytest.mark.parametrize(
        "domain,expected_host",
        [
            # hypothetical future regions: assume the modern app.<domain> scheme
            # that us3/eu2/ap3 follow
            ("us4.coralogix.com", "app.us4.coralogix.com"),
            ("eu3.coralogix.com", "app.eu3.coralogix.com"),
            ("me1.coralogix.com", "app.me1.coralogix.com"),
            ("gov2.coralogixgov.us", "app.gov2.coralogixgov.us"),
        ],
    )
    def test_unknown_coralogix_domain_assumes_app_prefix(self, domain, expected_host):
        """Unmapped Coralogix regions get the modern app.<domain> UI hostname."""
        config = CoralogixConfig(api_key="k", team_slug="acme", domain=domain)
        assert get_ui_base_url(config) == f"https://acme.{expected_host}"

    def test_custom_domain_containing_coralogix_is_not_app_prefixed(self):
        """A custom domain that merely contains 'coralogix' is used as-is."""
        config = CoralogixConfig(
            api_key="k", team_slug="acme", domain="logs.coralogix-proxy.internal"
        )
        assert get_ui_base_url(config) == "https://acme.logs.coralogix-proxy.internal"

    def test_domain_already_app_prefixed_is_not_double_prefixed(self):
        """A domain mistakenly set to the UI hostname doesn't get a second app. prefix."""
        config = CoralogixConfig(
            api_key="k", team_slug="acme", domain="app.us4.coralogix.com"
        )
        assert get_ui_base_url(config) == "https://acme.app.us4.coralogix.com"

    def test_no_team_slug_and_no_ui_url_returns_none(self):
        """Without team_slug or ui_url there is no UI base URL."""
        config = CoralogixConfig(api_key="k", domain="us2.coralogix.com")
        assert get_ui_base_url(config) is None

    def test_ui_url_override_is_used_verbatim(self):
        """A configured ui_url is used as the permalink base."""
        config = CoralogixConfig(
            api_key="k",
            domain="us2.coralogix.com",
            ui_url="https://acme.app.cx498.coralogix.com",
        )
        assert get_ui_base_url(config) == "https://acme.app.cx498.coralogix.com"

    def test_ui_url_override_wins_over_team_slug_and_domain(self):
        """ui_url takes precedence over team_slug + domain derivation."""
        config = CoralogixConfig(
            api_key="k",
            domain="eu2.coralogix.com",
            team_slug="other-team",
            ui_url="https://acme.app.cx498.coralogix.com",
        )
        assert get_ui_base_url(config) == "https://acme.app.cx498.coralogix.com"

    def test_ui_url_trailing_slash_is_stripped(self):
        """A trailing slash on ui_url is stripped."""
        config = CoralogixConfig(
            api_key="k",
            domain="us2.coralogix.com",
            ui_url="https://acme.app.cx498.coralogix.com/",
        )
        assert get_ui_base_url(config) == "https://acme.app.cx498.coralogix.com"

    def test_ui_url_without_scheme_gets_https(self):
        """A scheme-less ui_url gets an https:// prefix."""
        config = CoralogixConfig(
            api_key="k",
            domain="us2.coralogix.com",
            ui_url="acme.app.cx498.coralogix.com",
        )
        assert get_ui_base_url(config) == "https://acme.app.cx498.coralogix.com"

    def test_ui_url_with_uppercase_scheme_is_not_double_prefixed(self):
        """An uppercase scheme is recognized; no second https:// is prepended."""
        config = CoralogixConfig(
            api_key="k",
            domain="us2.coralogix.com",
            ui_url="HTTPS://acme.app.cx498.coralogix.com",
        )
        assert get_ui_base_url(config) == "HTTPS://acme.app.cx498.coralogix.com"

    def test_deprecated_team_hostname_field_gets_mapped_hostname(self):
        """Deprecated team_hostname configs also get the mapped UI hostname."""
        config = CoralogixConfig(
            api_key="k", domain="us2.coralogix.com", team_hostname="acme"
        )
        assert config.team_slug == "acme"
        assert "team_hostname" not in (config.model_extra or {})
        assert get_ui_base_url(config) == "https://acme.app.cx498.coralogix.com"

    def test_team_slug_wins_over_deprecated_team_hostname(self):
        """team_slug is preferred and the deprecated field is dropped when both are set."""
        config = CoralogixConfig(
            api_key="k",
            domain="us2.coralogix.com",
            team_slug="new-team",
            team_hostname="old-team",
        )
        assert config.team_slug == "new-team"
        assert "team_hostname" not in config.model_dump()
        assert get_ui_base_url(config) == "https://new-team.app.cx498.coralogix.com"


class TestUIPermalinkToolURL:
    """End-to-end (tool-level) permalink tests for ROB-1395."""

    PARAMS = {
        "query": "source logs | lucene 'error' | limit 100",
        "description": "test query",
        "query_type": "Logs",
        "start_date": "2024-01-01T00:00:00Z",
        "end_date": "2024-01-01T01:00:00Z",
    }

    def _invoke(self, config: CoralogixConfig):
        """Invoke the tool with the DataPrime API stubbed out."""
        toolset = CoralogixToolset()
        toolset.config = config
        tool = ExecuteDataPrimeQuery(toolset)
        with patch(
            "holmes.plugins.toolsets.coralogix.toolset_coralogix.execute_dataprime_query"
        ) as mock_execute:
            mock_execute.return_value = ([{"log": "test"}], None)
            return tool._invoke(self.PARAMS, Mock())

    def test_us2_permalink_points_to_team_ui_hostname(self):
        """US2 tool invocations produce permalinks on the official UI hostname."""
        result = self._invoke(
            CoralogixConfig(api_key="k", team_slug="acme", domain="us2.coralogix.com")
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.url is not None
        assert result.url.startswith(
            "https://acme.app.cx498.coralogix.com/#/query-new/logs?"
        )
        # the API domain must not leak into the UI link
        assert "us2.coralogix.com" not in result.url

    def test_ui_url_override_used_for_permalink(self):
        """ui_url override is reflected in the tool result URL."""
        result = self._invoke(
            CoralogixConfig(
                api_key="k",
                domain="us2.coralogix.com",
                ui_url="https://acme.app.cx498.coralogix.com",
            )
        )
        assert result.url is not None
        assert result.url.startswith(
            "https://acme.app.cx498.coralogix.com/#/query-new/logs?"
        )

    def test_no_team_slug_no_ui_url_yields_no_permalink_but_succeeds(self):
        """Missing team_slug/ui_url yields no URL but the tool still succeeds."""
        result = self._invoke(CoralogixConfig(api_key="k", domain="us2.coralogix.com"))
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.url is None


class TestExecuteDataPrimeQuery:
    """Tests for execute_dataprime_query function."""

    @patch("holmes.plugins.toolsets.coralogix.api.execute_coralogix_query")
    def test_valid_results(self, mock_query):
        """Test execute_dataprime_query with valid results."""
        # Real Coralogix response format: NDJSON with result.results structure
        real_response = {
            "result": {
                "results": [
                    {
                        "metadata": [
                            {
                                "key": "timestamp",
                                "value": "2025-03-25T07:26:33.577000000",
                            },
                            {"key": "severity", "value": "1"},
                        ],
                        "labels": [
                            {"key": "applicationname", "value": "default"},
                            {"key": "subsystemname", "value": "checkout-service"},
                        ],
                        "userData": json.dumps(
                            {
                                "kubernetes": {
                                    "namespace_name": "default",
                                    "pod_name": "checkout-service-5bcd6bf54-g8ckl",
                                },
                                "log": "Processing payment request",
                                "time": "2025-03-25T07:26:33.577000000Z",
                            }
                        ),
                    },
                    {
                        "metadata": [
                            {
                                "key": "timestamp",
                                "value": "2025-03-25T07:26:34.123000000",
                            },
                            {"key": "severity", "value": "5"},
                        ],
                        "labels": [
                            {"key": "applicationname", "value": "default"},
                            {"key": "subsystemname", "value": "payment-service"},
                        ],
                        "userData": json.dumps(
                            {
                                "kubernetes": {
                                    "namespace_name": "default",
                                    "pod_name": "payment-service-abc123",
                                },
                                "log": "Payment completed successfully",
                                "time": "2025-03-25T07:26:34.123000000Z",
                            }
                        ),
                    },
                ]
            }
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(real_response)
        mock_query.return_value = (mock_response, "https://test.com/api")

        result, error = execute_dataprime_query(
            domain="test.com",
            api_key="test_key",
            dataprime_query="source logs | limit 10",
        )

        assert error is None
        assert isinstance(result, list)
        assert len(result) == 2
        # After cleanup, userData should be replaced with parsed JSON
        assert isinstance(result[0], dict)
        assert "log" in result[0] or "kubernetes" in result[0]

    @patch("holmes.plugins.toolsets.coralogix.api.execute_coralogix_query")
    def test_empty_response(self, mock_query):
        """Test execute_dataprime_query with empty response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = ""
        mock_query.return_value = (mock_response, "https://test.com/api")

        result, error = execute_dataprime_query(
            domain="test.com",
            api_key="test_key",
            dataprime_query="source logs | limit 10",
        )

        assert error is not None
        assert "Empty 200 response" in error

    @patch("holmes.plugins.toolsets.coralogix.api.execute_coralogix_query")
    def test_compilation_error(self, mock_query):
        """Test execute_dataprime_query with compilation error."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Compiler error: Invalid syntax"
        mock_query.return_value = (mock_response, "https://test.com/api")

        result, error = execute_dataprime_query(
            domain="test.com",
            api_key="test_key",
            dataprime_query="source logs | filter invalid",
        )

        assert result is None
        assert error is not None
        assert "Compilation errors" in error

    @patch("holmes.plugins.toolsets.coralogix.api.execute_coralogix_query")
    def test_error_response(self, mock_query):
        """Test execute_dataprime_query with error response."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_query.return_value = (mock_response, "https://test.com/api")

        result, error = execute_dataprime_query(
            domain="test.com",
            api_key="test_key",
            dataprime_query="source logs | limit 10",
        )

        assert result is None
        assert error is not None
        assert "status_code=500" in error

    @patch("holmes.plugins.toolsets.coralogix.api.execute_coralogix_query")
    def test_userdata_replacement(self, mock_query):
        """Test execute_dataprime_query with userData replacement."""
        user_data_json = json.dumps(
            {"log": "replaced", "timestamp": "2024-01-01T00:00:00Z"}
        )
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(
            {"result": {"results": [{"userData": user_data_json}]}}
        )
        mock_query.return_value = (mock_response, "https://test.com/api")

        result, error = execute_dataprime_query(
            domain="test.com",
            api_key="test_key",
            dataprime_query="source logs | limit 10",
        )

        assert error is None
        assert isinstance(result, list)
        if result and isinstance(result[0], dict):
            assert "log" in result[0] or "timestamp" in result[0]

    @patch("holmes.plugins.toolsets.coralogix.api.execute_coralogix_query")
    def test_exception_handling(self, mock_query):
        """Test execute_dataprime_query with exception."""
        mock_query.side_effect = Exception("Network error")

        # Suppress error logging for this test
        with patch("holmes.plugins.toolsets.coralogix.api.logging.error"):
            result, error = execute_dataprime_query(
                domain="test.com",
                api_key="test_key",
                dataprime_query="source logs | limit 10",
            )

        assert result is None
        assert error is not None
        assert "Network error" in error


class TestExecuteDataPrimeQueryTool:
    """Tests for ExecuteDataPrimeQuery tool class."""

    @pytest.fixture
    def tool(self, coralogix_toolset):
        return ExecuteDataPrimeQuery(coralogix_toolset)

    def test_invalid_tier(self, tool):
        """Test ExecuteDataPrimeQuery with invalid tier."""
        params = {
            "query": "source logs | limit 10",
            "description": "test query",
            "query_type": "Logs",
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-01-01T23:59:59Z",
            "tier": "INVALID_TIER",
        }

        result = tool._invoke(params, Mock())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid tier" in result.error

    def test_invalid_date(self, tool):
        """Test ExecuteDataPrimeQuery with invalid date."""
        params = {
            "query": "source logs | limit 10",
            "description": "test query",
            "query_type": "Logs",
            "start_date": "",
            "end_date": "2024-01-01T23:59:59Z",
            "tier": None,
        }

        result = tool._invoke(params, Mock())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid start or end date" in result.error

    def test_swapped_dates(self, tool):
        """Test ExecuteDataPrimeQuery with swapped dates."""
        params = {
            "query": "source logs | limit 10",
            "description": "test query",
            "query_type": "Logs",
            "start_date": "2024-01-02T00:00:00Z",
            "end_date": "2024-01-01T00:00:00Z",
            "tier": None,
        }

        with patch(
            "holmes.plugins.toolsets.coralogix.toolset_coralogix.execute_dataprime_query"
        ) as mock_execute:
            mock_execute.return_value = ([{"result": "test"}], None)
            _ = tool._invoke(params, Mock())

            call_args = mock_execute.call_args
            assert call_args[1]["start_date"] < call_args[1]["end_date"]

    def test_valid_tier(self, tool):
        """Test ExecuteDataPrimeQuery with valid tier."""
        params = {
            "query": "source logs | limit 10",
            "description": "test query",
            "query_type": "Logs",
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-01-01T23:59:59Z",
            "tier": "FREQUENT_SEARCH",
        }

        with patch(
            "holmes.plugins.toolsets.coralogix.toolset_coralogix.execute_dataprime_query"
        ) as mock_execute:
            mock_execute.return_value = ([{"result": "test"}], None)
            result = tool._invoke(params, Mock())

            assert result.status == StructuredToolResultStatus.SUCCESS
            call_args = mock_execute.call_args
            assert call_args[1]["tier"] == CoralogixTier.FREQUENT_SEARCH

    def test_no_config(self, tool):
        """Test ExecuteDataPrimeQuery without toolset configuration."""
        tool._toolset.config = None
        params = {
            "query": "source logs | limit 10",
            "description": "test query",
            "query_type": "Logs",
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-01-01T23:59:59Z",
            "tier": None,
        }

        result = tool._invoke(params, Mock())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not configured" in result.error

    def test_query_error(self, tool):
        """Test ExecuteDataPrimeQuery when query execution returns error."""
        params = {
            "query": "source logs | limit 10",
            "description": "test query",
            "query_type": "Logs",
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-01-01T23:59:59Z",
            "tier": None,
        }

        with patch(
            "holmes.plugins.toolsets.coralogix.toolset_coralogix.execute_dataprime_query"
        ) as mock_execute:
            mock_execute.return_value = (None, "Query failed")
            result = tool._invoke(params, Mock())

            assert result.status == StructuredToolResultStatus.ERROR
            assert result.error == "Query failed"
