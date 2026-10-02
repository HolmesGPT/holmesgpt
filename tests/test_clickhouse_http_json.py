"""Tests for optional ClickHouse HTTP JSONEachRow query path."""

import pytest
from pydantic import ValidationError

sqlalchemy = pytest.importorskip("sqlalchemy")

from holmes.plugins.toolsets.database.database import (  # noqa: E402
    DatabaseConfig,
    _execute_clickhouse_http,
    _parse_clickhouse_http_url,
)

pytestmark = getattr(pytest.mark, "db-connectors")


class TestClickhouseHttpJsonConfig:
    def test_default_disabled(self):
        config = DatabaseConfig(
            connection_url="clickhouse+http://u:p@host:8123/otel"
        )
        assert config.clickhouse_use_http_json is False

    def test_enabled_for_clickhouse_url(self):
        config = DatabaseConfig(
            connection_url="clickhouse+http://u:p@host:8123/otel",
            clickhouse_use_http_json=True,
        )
        assert config.clickhouse_use_http_json is True

    def test_requires_clickhouse_url(self):
        with pytest.raises(ValidationError):
            DatabaseConfig(
                connection_url="postgresql://u:p@host/db",
                clickhouse_use_http_json=True,
            )


class TestParseClickhouseHttpUrl:
    def test_parse_with_auth(self):
        base, db, auth = _parse_clickhouse_http_url(
            "clickhouse+http://user:secret@ch.example:8123/otel"
        )
        assert base == "http://ch.example:8123"
        assert db == "otel"
        assert auth == ("user", "secret")

    def test_parse_default_port_and_database(self):
        base, db, auth = _parse_clickhouse_http_url(
            "clickhouse+http://localhost/logs"
        )
        assert base == "http://localhost:8123"
        assert db == "logs"
        assert auth is None

    def test_parse_user_with_empty_password(self):
        # ClickHouse 'default' user often has no password; auth must still be sent.
        _, _, auth = _parse_clickhouse_http_url(
            "clickhouse+http://default@ch:8123/otel"
        )
        assert auth == ("default", "")

    def test_parse_percent_encoded_password(self):
        # Password contains '@' encoded as %40.
        _, _, auth = _parse_clickhouse_http_url(
            "clickhouse+http://user:p%40ss@ch:8123/otel"
        )
        assert auth == ("user", "p@ss")

    def test_parse_ipv6_host(self):
        base, _, _ = _parse_clickhouse_http_url(
            "clickhouse+http://[::1]:8123/otel"
        )
        assert base == "http://[::1]:8123"

    def test_parse_https_default_port(self):
        base, _, _ = _parse_clickhouse_http_url(
            "clickhouse+https://ch.example/otel"
        )
        assert base == "https://ch.example:8443"


class TestExecuteClickhouseHttp:
    def test_parses_jsoneachrow_and_request_shape(self, responses):
        body = (
            '{"Timestamp":"2026-03-22T14:26:52.123456789Z","Body":"err"}\n'
            '{"Timestamp":"2026-03-22T14:26:53.000000000Z","Body":"ok"}\n'
        )
        responses.add(
            responses.POST,
            "http://localhost:8123/",
            body=body,
            status=200,
        )

        result = _execute_clickhouse_http(
            "http://localhost:8123",
            "otel",
            None,
            "SELECT Timestamp, Body FROM otel.logs LIMIT 2",
            effective_limit=10,
        )

        assert result["columns"] == ["Timestamp", "Body"]
        assert result["row_count"] == 2
        # Nanosecond precision preserved as a string (the whole point of this path).
        assert "123456789" in str(result["rows"][0][0])
        assert result["truncated"] is False

        assert len(responses.calls) == 1
        request = responses.calls[0].request
        assert "database=otel" in request.url
        assert "default_format=JSONEachRow" in request.url
        assert request.body == b"SELECT Timestamp, Body FROM otel.logs LIMIT 2"
        assert "Authorization" not in request.headers

    def test_auth_header_sent_when_credentials_provided(self, responses):
        responses.add(
            responses.POST,
            "http://localhost:8123/",
            body='{"x":1}\n',
            status=200,
        )

        _execute_clickhouse_http(
            "http://localhost:8123",
            "db",
            ("user", "secret"),
            "SELECT 1",
            effective_limit=1,
        )

        auth_header = responses.calls[0].request.headers.get("Authorization", "")
        assert auth_header.startswith("Basic ")

    def test_truncates_at_limit(self, responses):
        body = "\n".join(f'{{"id":{i}}}' for i in range(5)) + "\n"
        responses.add(
            responses.POST,
            "http://localhost:8123/",
            body=body,
            status=200,
        )

        result = _execute_clickhouse_http(
            "http://localhost:8123",
            "db",
            None,
            "SELECT * FROM t",
            effective_limit=2,
        )

        assert result["row_count"] == 2
        assert result["truncated"] is True

    def test_http_error_includes_sql_and_target(self, responses):
        responses.add(
            responses.POST,
            "http://localhost:8123/",
            body="Code: 60. Table not found",
            status=400,
        )

        with pytest.raises(ValueError) as exc_info:
            _execute_clickhouse_http(
                "http://localhost:8123",
                "otel",
                None,
                "SELECT * FROM does_not_exist",
                effective_limit=10,
            )

        msg = str(exc_info.value)
        assert "400" in msg
        assert "otel" in msg
        assert "SELECT * FROM does_not_exist" in msg

    def test_connection_error_wrapped(self, responses):
        import requests as _requests

        responses.add(
            responses.POST,
            "http://localhost:8123/",
            body=_requests.exceptions.ConnectionError("boom"),
        )

        with pytest.raises(ValueError) as exc_info:
            _execute_clickhouse_http(
                "http://localhost:8123",
                "db",
                None,
                "SELECT 1",
                effective_limit=10,
            )

        msg = str(exc_info.value)
        assert "ClickHouse connection error" in msg
        assert "SELECT 1" in msg


class TestClickhouseSqlalchemyDatetimeBug:
    """Documents the upstream clickhouse-sqlalchemy TSV DateTime64 bug that
    motivated this opt-in HTTP JSONEachRow path. Skipped if the (internal)
    converter symbol moves in a future driver release."""

    @pytest.fixture
    def datetime_converter(self):
        pytest.importorskip("clickhouse_sqlalchemy")
        try:
            from clickhouse_sqlalchemy.drivers.http.transport import (
                datetime_converter,
            )
        except ImportError:
            pytest.skip(
                "clickhouse_sqlalchemy.drivers.http.transport.datetime_converter "
                "is not importable in this version"
            )
        return datetime_converter

    def test_datetime_converter_fails_on_nanoseconds(self, datetime_converter):
        with pytest.raises(ValueError, match="unconverted data remains"):
            datetime_converter("2026-03-22 14:26:52.123456789")

    def test_datetime_converter_ok_microseconds(self, datetime_converter):
        dt = datetime_converter("2026-03-22 14:26:52.123456")
        assert dt.year == 2026
