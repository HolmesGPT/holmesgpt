import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.airflow.airflow import AirflowConfig, AirflowToolset
from holmes.plugins.toolsets.flink.flink import FlinkConfig, FlinkToolset
from holmes.plugins.toolsets.trino.trino import TrinoConfig, TrinoToolset
from tests.conftest import create_mock_tool_invoke_context


def _tool(toolset, name):
    return next(tool for tool in toolset.tools if tool.name == name)


class TestTrinoToolset:
    def test_rejects_mutating_sql_without_network_request(self):
        toolset = TrinoToolset()
        toolset.config = TrinoConfig(api_url="http://trino:8080")

        result = _tool(toolset, "trino_query").invoke(
            {"query": "DROP TABLE production.orders"},
            create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "read-only" in result.error

    @responses.activate
    def test_returns_bounded_query_results_and_cancels_remaining_pages(self):
        toolset = TrinoToolset()
        toolset.config = TrinoConfig(
            api_url="http://trino:8080", trino_user="sre", max_rows=2
        )
        responses.post(
            "http://trino:8080/v1/statement",
            json={
                "id": "query-1",
                "columns": [{"name": "state", "type": "varchar"}],
                "data": [["RUNNING"], ["FAILED"]],
                "nextUri": "http://trino:8080/v1/statement/query-1/2",
                "infoUri": "http://trino:8080/ui/query.html?query-1",
                "stats": {"state": "RUNNING"},
            },
        )
        responses.delete("http://trino:8080/v1/statement/query-1/2", json={})

        result = _tool(toolset, "trino_query").invoke(
            {"query": "SELECT state FROM system.runtime.queries LIMIT 2"},
            create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["rows"] == [["RUNNING"], ["FAILED"]]
        assert result.data["truncated"] is True
        assert responses.calls[0].request.headers["X-Trino-User"] == "sre"
        assert responses.calls[1].request.method == "DELETE"


class TestFlinkToolset:
    @responses.activate
    def test_list_jobs_bounds_large_response(self):
        toolset = FlinkToolset()
        toolset.config = FlinkConfig(api_url="http://flink:8081", max_items=2)
        responses.get(
            "http://flink:8081/jobs/overview",
            json={
                "jobs": [
                    {"jid": "1", "state": "RUNNING"},
                    {"jid": "2", "state": "FAILED"},
                    {"jid": "3", "state": "FINISHED"},
                ]
            },
        )

        result = _tool(toolset, "flink_list_jobs").invoke(
            {}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["jobs"] == [
            {"jid": "1", "state": "RUNNING"},
            {"jid": "2", "state": "FAILED"},
        ]
        assert result.data["holmes_truncated"] is True

    @responses.activate
    def test_exception_error_includes_endpoint_and_response(self):
        toolset = FlinkToolset()
        toolset.config = FlinkConfig(api_url="http://flink:8081")
        responses.get(
            "http://flink:8081/jobs/deadbeef/exceptions",
            status=404,
            body="Unknown job",
        )

        result = _tool(toolset, "flink_get_job_exceptions").invoke(
            {"job_id": "deadbeef"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "/jobs/deadbeef/exceptions" in result.error
        assert "Unknown job" in result.error

    @responses.activate
    def test_job_details_bound_vertices(self):
        toolset = FlinkToolset()
        toolset.config = FlinkConfig(api_url="http://flink:8081", max_items=1)
        responses.get(
            "http://flink:8081/jobs/deadbeef",
            json={
                "jid": "deadbeef",
                "vertices": [{"id": "source"}, {"id": "sink"}],
            },
        )

        result = _tool(toolset, "flink_get_job").invoke(
            {"job_id": "deadbeef"}, create_mock_tool_invoke_context()
        )

        assert result.data["vertices"] == [{"id": "source"}]
        assert result.data["holmes_truncated"] is True


class TestAirflowToolset:
    @responses.activate
    def test_list_dag_runs_uses_bounded_pagination(self):
        toolset = AirflowToolset()
        toolset.config = AirflowConfig(
            api_url="http://airflow:8080", api_version="v2", max_items=25
        )
        responses.get(
            "http://airflow:8080/api/v2/dags/orders%2Fdaily/dagRuns",
            json={"dag_runs": [{"dag_run_id": "scheduled__1", "state": "failed"}]},
        )

        result = _tool(toolset, "airflow_list_dag_runs").invoke(
            {"dag_id": "orders/daily", "limit": 500},
            create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["dag_runs"] == [
            {"dag_run_id": "scheduled__1", "state": "failed"}
        ]
        assert responses.calls[0].request.params["limit"] == "25"
        assert responses.calls[0].request.params["order_by"] == "-logical_date"

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            (
                {
                    "api_url": "http://airflow:8080",
                    "bearer_token": "token",
                    "username": "user",
                    "password": "password",
                },
                "either bearer token or basic auth",
            ),
            (
                {"api_url": "http://airflow:8080", "username": "user"},
                "password is required",
            ),
        ],
    )
    def test_rejects_invalid_auth_config(self, config, message):
        with pytest.raises(ValueError, match=message):
            AirflowConfig(**config)
