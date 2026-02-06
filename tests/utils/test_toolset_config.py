"""Tests for ToolsetConfig base class and deprecated field mappings."""

import logging
from typing import ClassVar, Dict, Optional

from pydantic import Field

from holmes.plugins.toolsets.newrelic.newrelic import NewrelicConfig
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusConfig
from holmes.utils.pydantic_utils import ToolsetConfig


class SampleConfig(ToolsetConfig):
    """Sample config class for testing deprecated mappings."""

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "old_field": "new_field",
        "another_old": "another_new",
        "removed_field": None,
    }

    new_field: str = Field(default="default_value")
    another_new: int = Field(default=10)
    unchanged_field: str = Field(default="unchanged")


class TestToolsetConfig:
    """Tests for ToolsetConfig base class."""

    def test_new_field_names_work(self):
        """Test that new field names work without warnings."""
        config = SampleConfig(new_field="test", another_new=20)
        assert config.new_field == "test"
        assert config.another_new == 20

    def test_deprecated_field_name_migrated(self, caplog):
        """Test that deprecated field names are migrated to new names."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(old_field="migrated_value")

        assert config.new_field == "migrated_value"
        assert "old_field -> new_field" in caplog.text

    def test_multiple_deprecated_fields(self, caplog):
        """Test that multiple deprecated fields are migrated."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(old_field="value1", another_old=42)

        assert config.new_field == "value1"
        assert config.another_new == 42
        assert "old_field -> new_field" in caplog.text
        assert "another_old -> another_new" in caplog.text

    def test_new_field_takes_precedence(self, caplog):
        """Test that new field takes precedence over deprecated field."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(old_field="old_value", new_field="new_value")

        # New field should take precedence
        assert config.new_field == "new_value"

    def test_removed_field_logged(self, caplog):
        """Test that removed fields are logged but not cause errors."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(removed_field="some_value")

        # Config should still be valid
        assert config.new_field == "default_value"
        assert "removed_field (removed)" in caplog.text

    def test_no_warning_for_new_fields(self, caplog):
        """Test that using new field names doesn't trigger warnings."""
        with caplog.at_level(logging.WARNING):
            _ = SampleConfig(new_field="test", another_new=5)

        assert "deprecated" not in caplog.text.lower()

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed (for forward compatibility)."""
        config = SampleConfig(new_field="test", unknown_future_field="value")
        assert config.new_field == "test"

    def test_unchanged_field_works(self):
        """Test that fields without deprecation mappings work normally."""
        config = SampleConfig(unchanged_field="custom")
        assert config.unchanged_field == "custom"


class TestPrometheusConfigBackwardCompatibility:
    """Test backward compatibility for PrometheusConfig deprecated fields."""

    def test_deprecated_prometheus_fields(self, caplog):
        """Test that deprecated Prometheus config fields are migrated."""
        with caplog.at_level(logging.WARNING):
            config = PrometheusConfig(
                prometheus_url="http://prometheus:9090",
                default_query_timeout_seconds=45,
                prometheus_ssl_enabled=False,
            )

        assert config.query_timeout_seconds_default == 45
        assert config.verify_ssl is False
        assert (
            "default_query_timeout_seconds -> query_timeout_seconds_default"
            in caplog.text
        )
        assert "prometheus_ssl_enabled -> verify_ssl" in caplog.text

    def test_new_prometheus_fields_no_warning(self, caplog):
        """Test that new Prometheus field names don't trigger warnings."""
        with caplog.at_level(logging.WARNING):
            config = PrometheusConfig(
                prometheus_url="http://prometheus:9090",
                query_timeout_seconds_default=30,
                verify_ssl=True,
            )

        assert config.query_timeout_seconds_default == 30
        assert "deprecated" not in caplog.text.lower()


class TestServiceNowConfigBackwardCompatibility:
    """Test backward compatibility for ServiceNowTablesConfig deprecated fields."""

    def test_deprecated_servicenow_fields(self, caplog):
        """Test that deprecated ServiceNow config fields are migrated."""
        from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
            ServiceNowTablesConfig,
        )

        with caplog.at_level(logging.WARNING):
            old_config = ServiceNowTablesConfig(
                api_key="now_test123",
                instance_url="https://test.service-now.com",
            )

        # Verify field was migrated
        assert old_config.api_url == "https://test.service-now.com"
        assert "instance_url -> api_url" in caplog.text

    def test_new_servicenow_fields_no_warning(self, caplog):
        """Test that new ServiceNow field names don't trigger warnings."""
        from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
            ServiceNowTablesConfig,
        )

        with caplog.at_level(logging.WARNING):
            new_config = ServiceNowTablesConfig(
                api_key="now_test123",
                api_url="https://test.service-now.com",
            )

        assert new_config.api_url == "https://test.service-now.com"
        assert "deprecated" not in caplog.text.lower()

    def test_deprecated_and_new_servicenow_configs_equivalent(self, caplog):
        """Test that configs created with old and new fields are equivalent."""
        from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
            ServiceNowTablesConfig,
        )

        # Create config using deprecated field name
        with caplog.at_level(logging.WARNING):
            old_config = ServiceNowTablesConfig(
                api_key="now_test123",
                instance_url="https://test.service-now.com",
                api_key_header="custom-header",
            )

        caplog.clear()

        # Create config using new field name
        with caplog.at_level(logging.WARNING):
            new_config = ServiceNowTablesConfig(
                api_key="now_test123",
                api_url="https://test.service-now.com",
                api_key_header="custom-header",
            )

        # Verify both configs have the same values
        assert old_config.api_key == new_config.api_key
        assert old_config.api_url == new_config.api_url
        assert old_config.api_key_header == new_config.api_key_header

        # Verify model_dump() produces equivalent output (excluding model_extra)
        old_dump = {
            k: v for k, v in old_config.model_dump().items() if k != "model_extra"
        }
        new_dump = {
            k: v for k, v in new_config.model_dump().items() if k != "model_extra"
        }
        assert old_dump == new_dump


class TestNewrelicConfigBackwardCompatibility:
    """Test backward compatibility for NewrelicConfig deprecated fields."""

    def test_deprecated_newrelic_fields(self, caplog):
        """Test that deprecated New Relic config fields are migrated."""
        with caplog.at_level(logging.WARNING):
            config = NewrelicConfig(
                nr_api_key="NRAK-TESTKEY123",
                nr_account_id="1234567",
            )

        assert config.api_key == "NRAK-TESTKEY123"
        assert config.account_id == "1234567"
        assert "nr_api_key -> api_key" in caplog.text
        assert "nr_account_id -> account_id" in caplog.text

    def test_new_newrelic_fields_no_warning(self, caplog):
        """Test that new New Relic field names don't trigger warnings."""
        with caplog.at_level(logging.WARNING):
            config = NewrelicConfig(
                api_key="NRAK-TESTKEY123",
                account_id="1234567",
            )

        assert config.api_key == "NRAK-TESTKEY123"
        assert config.account_id == "1234567"
        assert "deprecated" not in caplog.text.lower()

    def test_old_and_new_fields_mixed(self, caplog):
        """Test that deprecated and new configs produce equivalent results."""
        # Create config with old field names
        with caplog.at_level(logging.WARNING):
            old_config = NewrelicConfig(
                nr_api_key="NRAK-TESTKEY123",
                nr_account_id="1234567",
                is_eu_datacenter=True,
            )

        # Create config with new field names
        new_config = NewrelicConfig(
            api_key="NRAK-TESTKEY123",
            account_id="1234567",
            is_eu_datacenter=True,
        )

        # Both should produce the same result
        assert old_config.api_key == new_config.api_key
        assert old_config.account_id == new_config.account_id
        assert old_config.is_eu_datacenter == new_config.is_eu_datacenter
