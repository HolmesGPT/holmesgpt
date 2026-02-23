"""Unit tests for the database toolset."""

import os
import tempfile

import pytest
from pydantic import ValidationError

sqlalchemy = pytest.importorskip("sqlalchemy")

from holmes.plugins.toolsets.database.database import (  # noqa: E402
    DatabaseConfig,
    DatabaseToolset,
    _READONLY_PATTERN,
    _WRITE_ANYWHERE_PATTERN,
    _WRITE_PATTERN,
    _normalise_url,
    _serialize_value,
)

pytestmark = getattr(pytest.mark, "db-connectors")


class TestNormaliseUrl:
    def test_postgres_url(self):
        assert (
            _normalise_url("postgresql://user:pass@host/db")
            == "postgresql+pg8000://user:pass@host/db"
        )

    def test_postgres_short_scheme(self):
        assert (
            _normalise_url("postgres://user:pass@host/db")
            == "postgresql+pg8000://user:pass@host/db"
        )

    def test_mysql_url(self):
        assert (
            _normalise_url("mysql://user:pass@host/db")
            == "mysql+pymysql://user:pass@host/db"
        )

    def test_mariadb_url(self):
        assert (
            _normalise_url("mariadb://user:pass@host/db")
            == "mysql+pymysql://user:pass@host/db"
        )

    def test_mssql_url(self):
        assert (
            _normalise_url("mssql://user:pass@host/db")
            == "mssql+pymssql://user:pass@host/db"
        )

    def test_sqlite_url(self):
        assert _normalise_url("sqlite:///path/to/db") == "sqlite:///path/to/db"

    def test_already_correct_driver(self):
        assert (
            _normalise_url("postgresql+pg8000://user:pass@host/db")
            == "postgresql+pg8000://user:pass@host/db"
        )

    def test_already_correct_pymysql(self):
        assert (
            _normalise_url("mysql+pymysql://user:pass@host/db")
            == "mysql+pymysql://user:pass@host/db"
        )

    def test_unknown_scheme_passthrough(self):
        url = "cockroachdb://user:pass@host/db"
        assert _normalise_url(url) == url

    def test_mysql_mysqldb_rewritten(self):
        assert (
            _normalise_url("mysql+mysqldb://user:pass@host/db")
            == "mysql+pymysql://user:pass@host/db"
        )


class TestSerializeValue:
    def test_none(self):
        assert _serialize_value(None) is None

    def test_primitives(self):
        assert _serialize_value(42) == 42
        assert _serialize_value(3.14) == 3.14
        assert _serialize_value(True) is True
        assert _serialize_value("hello") == "hello"

    def test_bytes(self):
        assert _serialize_value(b"\xde\xad") == "dead"

    def test_dict_passthrough(self):
        d = {"key": "value"}
        assert _serialize_value(d) == d

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert _serialize_value(lst) == lst

    def test_datetime_to_str(self):
        from datetime import datetime

        dt = datetime(2024, 1, 15, 12, 0, 0)
        assert _serialize_value(dt) == "2024-01-15 12:00:00"

    def test_decimal_to_str(self):
        from decimal import Decimal

        assert _serialize_value(Decimal("99.95")) == "99.95"


class TestDatabaseConfig:
    def test_basic_config(self):
        config = DatabaseConfig(connection_url="postgresql://user:pass@host/db")
        assert config.connection_url == "postgresql://user:pass@host/db"

    def test_config_requires_url(self):
        with pytest.raises(ValidationError):
            DatabaseConfig()  # type: ignore[call-arg]


class TestDatabaseToolset:
    def test_toolset_name(self):
        toolset = DatabaseToolset()
        assert toolset.name == "database/sql"

    def test_toolset_has_tools(self):
        toolset = DatabaseToolset()
        tool_names = [t.name for t in toolset.tools]
        assert "database_sql_query" in tool_names
        assert "database_sql_list_tables" in tool_names
        assert "database_sql_describe_table" in tool_names

    def test_toolset_disabled_by_default(self):
        toolset = DatabaseToolset()
        assert toolset.enabled is False


class TestReadOnlyValidation:
    """Test that write operations are blocked."""

    def setup_method(self):
        self.toolset = DatabaseToolset()
        self.toolset.config = DatabaseConfig(connection_url="sqlite:///:memory:")

    def test_select_allowed_with_sqlite(self):
        """SELECT works end-to-end with a file-based SQLite database."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_file = tmp.name
        tmp.close()
        try:
            file_engine = sqlalchemy.create_engine(f"sqlite:///{db_file}")
            with file_engine.connect() as conn:
                conn.execute(
                    sqlalchemy.text("CREATE TABLE users (id INTEGER, name TEXT)")
                )
                conn.execute(sqlalchemy.text("INSERT INTO users VALUES (1, 'alice')"))
                conn.commit()
            file_engine.dispose()

            self.toolset.config = DatabaseConfig(connection_url=f"sqlite:///{db_file}")
            result = self.toolset.execute_query("SELECT * FROM users LIMIT 10")
            assert result["columns"] == ["id", "name"]
            assert len(result["rows"]) == 1
            assert result["rows"][0] == [1, "alice"]
            assert result["truncated"] is False
        finally:
            os.unlink(db_file)

    def test_insert_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("INSERT INTO users VALUES (1, 'test')")

    def test_update_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("UPDATE users SET name='x'")

    def test_delete_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("DELETE FROM users")

    def test_drop_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("DROP TABLE users")

    def test_truncate_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("TRUNCATE TABLE users")

    def test_create_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("CREATE TABLE foo (id int)")

    def test_alter_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query("ALTER TABLE users ADD COLUMN age int")

    def test_show_allowed(self):
        """SHOW statements should not be blocked."""
        assert _READONLY_PATTERN.match("SHOW TABLES")
        assert not _WRITE_PATTERN.match("SHOW TABLES")

    def test_describe_allowed(self):
        assert _READONLY_PATTERN.match("DESCRIBE users")
        assert not _WRITE_PATTERN.match("DESCRIBE users")

    def test_explain_allowed(self):
        assert _READONLY_PATTERN.match("EXPLAIN SELECT * FROM users")
        assert not _WRITE_PATTERN.match("EXPLAIN SELECT * FROM users")

    def test_with_cte_allowed(self):
        assert _READONLY_PATTERN.match("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert not _WRITE_PATTERN.match("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_writable_cte_blocked(self):
        """Writable CTEs (WITH ... DELETE/INSERT/UPDATE) must be blocked."""
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query(
                "WITH cte AS (DELETE FROM users RETURNING *) SELECT * FROM cte"
            )

    def test_writable_cte_insert_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query(
                "WITH new AS (INSERT INTO users VALUES (1) RETURNING *) SELECT * FROM new"
            )

    def test_writable_cte_update_blocked(self):
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            self.toolset.execute_query(
                "WITH upd AS (UPDATE users SET name='x' RETURNING *) SELECT * FROM upd"
            )

    def test_write_anywhere_pattern(self):
        """_WRITE_ANYWHERE_PATTERN catches write keywords inside CTEs."""
        assert _WRITE_ANYWHERE_PATTERN.search(
            "WITH cte AS (DELETE FROM users RETURNING *) SELECT * FROM cte"
        )
        assert not _WRITE_ANYWHERE_PATTERN.search("SELECT * FROM users")

    def test_random_command_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT"):
            self.toolset.execute_query("VACUUM ANALYZE")

    def test_case_insensitive_block(self):
        with pytest.raises(ValueError, match="Write operations"):
            self.toolset.execute_query("insert INTO users VALUES (1)")

    def test_whitespace_prefix_handled(self):
        with pytest.raises(ValueError, match="Write operations"):
            self.toolset.execute_query("   INSERT INTO users VALUES (1)")


class TestToolOneLiners:
    def test_query_one_liner(self):
        toolset = DatabaseToolset()
        query_tool = toolset.tools[0]  # DatabaseQuery
        result = query_tool.get_parameterized_one_liner(
            {"sql": "SELECT * FROM users WHERE active = true LIMIT 10"}
        )
        assert "Database" in result
        assert "SELECT" in result

    def test_list_tables_one_liner(self):
        toolset = DatabaseToolset()
        list_tool = toolset.tools[1]  # DatabaseListTables
        result = list_tool.get_parameterized_one_liner({"schema": "public"})
        assert "Database" in result
        assert "public" in result

    def test_describe_one_liner(self):
        toolset = DatabaseToolset()
        desc_tool = toolset.tools[2]  # DatabaseDescribeTable
        result = desc_tool.get_parameterized_one_liner({"table_name": "orders"})
        assert "Database" in result
        assert "orders" in result
