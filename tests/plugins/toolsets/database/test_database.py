import pytest

from holmes.plugins.toolsets.database.database import DatabaseConfig, DatabaseToolset


class TestDatabaseConfig:
    """Test DatabaseConfig class."""

    def test_read_only_default(self):
        """Test that read_only defaults to True."""
        config = DatabaseConfig(connection_url="sqlite:///:memory:")
        assert config.read_only is True

    def test_read_only_explicit_true(self):
        """Test that read_only can be explicitly set to True."""
        config = DatabaseConfig(
            connection_url="sqlite:///:memory:",
            read_only=True
        )
        assert config.read_only is True

    def test_read_only_explicit_false(self):
        """Test that read_only can be explicitly set to False."""
        config = DatabaseConfig(
            connection_url="sqlite:///:memory:",
            read_only=False
        )
        assert config.read_only is False

    def test_connection_url_required(self):
        """Test that connection_url is required."""
        with pytest.raises(Exception):
            DatabaseConfig()


class TestDatabaseToolset:
    """Test DatabaseToolset functionality."""

    @pytest.fixture
    def toolset(self):
        """Create a DatabaseToolset instance."""
        return DatabaseToolset()

    def test_read_only_mode_blocks_insert(self, toolset):
        """Test that INSERT is blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query("INSERT INTO test VALUES (1, 'data')")

    def test_read_only_mode_blocks_update(self, toolset):
        """Test that UPDATE is blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query("UPDATE test SET col = 'value' WHERE id = 1")

    def test_read_only_mode_blocks_delete(self, toolset):
        """Test that DELETE is blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query("DELETE FROM test WHERE id = 1")

    def test_read_only_mode_blocks_drop(self, toolset):
        """Test that DROP is blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query("DROP TABLE test")

    def test_read_only_mode_blocks_create(self, toolset):
        """Test that CREATE is blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query("CREATE TABLE test (id INT, name TEXT)")

    def test_read_only_mode_blocks_alter(self, toolset):
        """Test that ALTER is blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query("ALTER TABLE test ADD COLUMN new_col TEXT")

    def test_read_only_mode_blocks_writable_cte(self, toolset):
        """Test that writable CTEs are blocked in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        # Writable CTE (WITH ... DELETE)
        with pytest.raises(ValueError, match="Write operations are not allowed"):
            toolset.execute_query(
                "WITH cte AS (DELETE FROM users RETURNING *) SELECT * FROM cte"
            )

    def test_read_only_mode_allows_select(self, toolset):
        """Test that SELECT is allowed in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        result = toolset.execute_query("SELECT 1 AS test")
        assert result["columns"] == ["test"]
        assert result["rows"] == [[1]]

    def test_read_only_mode_allows_show(self, toolset):
        """Test that SHOW is allowed by read-only validation (even if database doesn't support it)."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        # The pattern matching should allow SHOW statements
        # SQLite doesn't support SHOW, so we'll get a SQL error, not a validation error
        try:
            toolset.execute_query("SHOW TABLES")
        except ValueError as e:
            # Should not get a validation error about write operations
            if "Write operations" in str(e):
                pytest.fail("SHOW statement incorrectly blocked as write operation")
        except Exception:
            # SQL errors are expected for SHOW on SQLite, validation passed
            pass

    def test_read_only_mode_allows_explain(self, toolset):
        """Test that EXPLAIN is allowed in read-only mode."""
        config = {"connection_url": "sqlite:///:memory:"}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        result = toolset.execute_query("EXPLAIN SELECT 1")
        assert result is not None

    def test_write_mode_allows_insert(self, toolset, tmp_path):
        """Test that INSERT is allowed when read_only=False."""
        db_file = tmp_path / "test.db"
        config = {"connection_url": f"sqlite:///{db_file}", "read_only": False}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        toolset.execute_query("CREATE TABLE test (id INT, name TEXT)")
        result = toolset.execute_query("INSERT INTO test VALUES (1, 'data')")
        assert result is not None
        assert "rows_affected" in result

    def test_write_mode_allows_update(self, toolset, tmp_path):
        """Test that UPDATE is allowed when read_only=False."""
        db_file = tmp_path / "test.db"
        config = {"connection_url": f"sqlite:///{db_file}", "read_only": False}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        toolset.execute_query("CREATE TABLE test (id INT, name TEXT)")
        toolset.execute_query("INSERT INTO test VALUES (1, 'original')")
        result = toolset.execute_query("UPDATE test SET name = 'updated' WHERE id = 1")
        assert result is not None
        assert "rows_affected" in result

    def test_write_mode_allows_delete(self, toolset, tmp_path):
        """Test that DELETE is allowed when read_only=False."""
        db_file = tmp_path / "test.db"
        config = {"connection_url": f"sqlite:///{db_file}", "read_only": False}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        toolset.execute_query("CREATE TABLE test (id INT, name TEXT)")
        toolset.execute_query("INSERT INTO test VALUES (1, 'data')")
        result = toolset.execute_query("DELETE FROM test WHERE id = 1")
        assert result is not None
        assert "rows_affected" in result

    def test_write_mode_allows_create(self, toolset):
        """Test that CREATE is allowed when read_only=False."""
        config = {"connection_url": "sqlite:///:memory:", "read_only": False}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        result = toolset.execute_query("CREATE TABLE test (id INT, name TEXT)")
        assert result is not None

    def test_write_mode_allows_alter(self, toolset, tmp_path):
        """Test that ALTER is allowed when read_only=False."""
        db_file = tmp_path / "test.db"
        config = {"connection_url": f"sqlite:///{db_file}", "read_only": False}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        toolset.execute_query("CREATE TABLE test (id INT)")
        result = toolset.execute_query("ALTER TABLE test ADD COLUMN name TEXT")
        assert result is not None

    def test_write_mode_allows_drop(self, toolset, tmp_path):
        """Test that DROP is allowed when read_only=False."""
        db_file = tmp_path / "test.db"
        config = {"connection_url": f"sqlite:///{db_file}", "read_only": False}
        success, _ = toolset.prerequisites_callable(config)
        assert success

        toolset.execute_query("CREATE TABLE test (id INT)")
        result = toolset.execute_query("DROP TABLE test")
        assert result is not None
