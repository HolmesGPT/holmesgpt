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
    extract_field,
    normalize_datetime,
)


@pytest.fixture
def coralogix_config():
    from holmes.plugins.toolsets.coralogix.utils import CoralogixLabelsConfig

    labels_config = CoralogixLabelsConfig(
        pod="kubernetes.pod_name",
        namespace="kubernetes.namespace_name",
        log_message="log",
        timestamp="time",
    )
    return CoralogixConfig(
        api_key="dummy_api_key",
        team_hostname="my-team",
        domain="eu2.coralogix.com",
        labels=labels_config,
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


@pytest.mark.parametrize(
    "data_obj, field, expected",
    [
        ({"key": "value"}, "key", "value"),
        ({"parent": {"child": "deep_value"}}, "parent.child", "deep_value"),
        ({}, "key", None),
    ],
)
def test_extract_field(data_obj, field, expected):
    assert extract_field(data_obj, field) == expected


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
