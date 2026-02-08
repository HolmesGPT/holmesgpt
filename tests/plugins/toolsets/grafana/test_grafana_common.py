from typing import Dict, Optional

import pytest

from holmes.plugins.toolsets.grafana.common import GrafanaConfig, build_headers


@pytest.mark.parametrize(
    "api_key, additional_headers, expected_headers",
    [
        (
            None,
            None,
            {"Accept": "application/json", "Content-Type": "application/json"},
        ),
        (
            "test_api_key_123",
            None,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_123",
            },
        ),
        (
            None,
            {"X-Request-ID": "req-abc"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Request-ID": "req-abc",
            },
        ),
        (
            "test_api_key_456",
            {"X-Custom-Header": "custom-value"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_456",
                "X-Custom-Header": "custom-value",
            },
        ),
        (
            None,
            {"Accept": "application/xml"},
            {"Accept": "application/xml", "Content-Type": "application/json"},
        ),
        (
            "test_api_key_789",
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Basic dXNlcjpwYXNz",
            },
        ),
        (
            "test_api_key_101",
            {},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_101",
            },
        ),
        (None, {}, {"Accept": "application/json", "Content-Type": "application/json"}),
    ],
)
def test_build_headers(
    api_key: Optional[str],
    additional_headers: Optional[Dict[str, str]],
    expected_headers: Dict[str, str],
):
    """Tests the build_headers function with various inputs."""
    result_headers = build_headers(api_key, additional_headers)
    assert result_headers == expected_headers


class TestGrafanaConfigBackwardCompatibility:
    """Tests for backward compatibility of deprecated field names in GrafanaConfig."""

    def test_deprecated_url_maps_to_api_url(self):
        """Test that deprecated 'url' field is mapped to 'api_url'."""
        # Using old field name
        old_config = GrafanaConfig(url="http://localhost:3000")  # type: ignore
        assert old_config.api_url == "http://localhost:3000"

    def test_deprecated_headers_maps_to_additional_headers(self):
        """Test that deprecated 'headers' field is mapped to 'additional_headers'."""
        # Using old field name
        old_config = GrafanaConfig(
            url="http://localhost:3000",  # type: ignore
            headers={"X-Custom": "value"},  # type: ignore
        )
        assert old_config.additional_headers == {"X-Custom": "value"}

    def test_new_field_names_work_directly(self):
        """Test that new field names work directly."""
        config = GrafanaConfig(
            api_url="http://localhost:3000",
            additional_headers={"Authorization": "Bearer token"},
        )
        assert config.api_url == "http://localhost:3000"
        assert config.additional_headers == {"Authorization": "Bearer token"}

    def test_old_and_new_configs_are_equivalent(self):
        """Test that configs created with old vs new field names produce equivalent results."""
        old_config = GrafanaConfig(
            url="http://grafana.example.com",  # type: ignore
            api_key="my-api-key",
            headers={"X-Scope-OrgID": "tenant-1"},  # type: ignore
            grafana_datasource_uid="loki-uid",
            verify_ssl=False,
        )

        new_config = GrafanaConfig(
            api_url="http://grafana.example.com",
            api_key="my-api-key",
            additional_headers={"X-Scope-OrgID": "tenant-1"},
            grafana_datasource_uid="loki-uid",
            verify_ssl=False,
        )

        # Compare all fields
        assert old_config.api_url == new_config.api_url
        assert old_config.api_key == new_config.api_key
        assert old_config.additional_headers == new_config.additional_headers
        assert old_config.grafana_datasource_uid == new_config.grafana_datasource_uid
        assert old_config.verify_ssl == new_config.verify_ssl

    def test_new_field_takes_precedence_over_old(self):
        """Test that new field name takes precedence when both are provided."""
        # When both old and new field names are provided, new field should take precedence
        config = GrafanaConfig(
            url="http://old-url.com",  # type: ignore
            api_url="http://new-url.com",
            headers={"X-Old": "old-value"},  # type: ignore
            additional_headers={"X-New": "new-value"},
        )

        # New field values should be used
        assert config.api_url == "http://new-url.com"
        assert config.additional_headers == {"X-New": "new-value"}
