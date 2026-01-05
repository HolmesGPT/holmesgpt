from unittest.mock import Mock, patch

from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.datadog.datadog_models import (
    DEFAULT_STORAGE_TIERS,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_logs import (
    DatadogLogsToolset,
)


class TestDatadogToolsetCheckPrerequisites:
    """Test cases for DatadogToolset.check_prerequisites() method"""

    def test_check_prerequisites_missing_config(self):
        """Test check_prerequisites with no config provided"""
        toolset = DatadogLogsToolset()
        toolset.config = None
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert (
            toolset.error
            == "Missing config for dd_api_key, dd_app_key, or site_api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/"
        )

    def test_check_prerequisites_empty_config(self):
        """Test check_prerequisites with empty config"""
        toolset = DatadogLogsToolset()
        toolset.config = {}
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert (
            toolset.error
            == "Missing config for dd_api_key, dd_app_key, or site_api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/"
        )

    def test_check_prerequisites_missing_required_fields(self):
        """Test check_prerequisites with missing required fields"""
        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "test-api-key",
            # Missing dd_app_key and site_api_url
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error
        assert "Failed to parse Datadog configuration" in toolset.error

    def test_check_prerequisites_invalid_config_format(self):
        """Test check_prerequisites with invalid config format"""
        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "test-api-key",
            "dd_app_key": "test-app-key",
            "site_api_url": "https://api.datadoghq.com",
            "storage_tiers": ["invalid-tier"],  # Invalid storage tier
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error
        assert "Failed to parse Datadog configuration" in toolset.error

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_logs.execute_datadog_http_request"
    )
    def test_check_prerequisites_successful_healthcheck(self, mock_execute_request):
        """Test check_prerequisites with successful healthcheck"""
        # Mock successful healthcheck response
        mock_result = Mock()
        mock_result.status = StructuredToolResultStatus.SUCCESS
        mock_result.error = None
        mock_execute_request.return_value = mock_result

        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "test-api-key",
            "dd_app_key": "test-app-key",
            "site_api_url": "https://api.datadoghq.com",
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.error is None  # Changed from "" to None
        assert toolset.dd_config is not None
        assert toolset.dd_config.dd_api_key == "test-api-key"
        assert toolset.dd_config.dd_app_key == "test-app-key"
        assert (
            str(toolset.dd_config.site_api_url).rstrip("/")
            == "https://api.datadoghq.com"
        )
        assert toolset.dd_config.storage_tiers == DEFAULT_STORAGE_TIERS

        # Verify healthcheck was called with correct params
        mock_execute_request.assert_called_once()

    def test_check_prerequisites_healthcheck_error(self):
        """Test check_prerequisites with healthcheck returning error"""
        # Mock healthcheck error response

        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "invalid-api-key",
            "dd_app_key": "invalid-app-key",
            "site_api_url": "https://api.datadoghq.com",
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error == 'Datadog API error: 401 - {"errors":["Unauthorized"]}'

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_logs.execute_datadog_http_request"
    )
    def test_check_prerequisites_healthcheck_exception(self, mock_execute_request):
        """Test check_prerequisites with healthcheck throwing exception"""
        # Mock execute_datadog_http_request to raise an exception
        mock_execute_request.side_effect = Exception("Network error")

        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "test-api-key",
            "dd_app_key": "test-app-key",
            "site_api_url": "https://api.datadoghq.com",
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert "Healthcheck failed with exception: Network error" in toolset.error

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_logs.execute_datadog_http_request"
    )
    def test_check_prerequisites_with_custom_config(self, mock_execute_request):
        """Test check_prerequisites with custom configuration"""
        mock_execute_request.return_value = {}
        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "test-api-key",
            "dd_app_key": "test-app-key",
            "site_api_url": "https://api.us3.datadoghq.com",
            "indexes": ["main", "secondary"],
            "storage_tiers": ["indexes", "flex"],
            "labels": {"pod": "custom_pod_name", "namespace": "custom_namespace"},
            "page_size": 500,
            "default_limit": 2000,
            "request_timeout": 120,
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.error is None  # Changed from "" to None
        assert toolset.dd_config is not None
        assert (
            str(toolset.dd_config.site_api_url).rstrip("/")
            == "https://api.us3.datadoghq.com"
        )
        assert toolset.dd_config.indexes == ["main", "secondary"]

    def test_check_prerequisites_with_empty_storage_tiers(self):
        """Test check_prerequisites with empty storage_tiers should fail validation"""
        toolset = DatadogLogsToolset()
        toolset.config = {
            "dd_api_key": "test-api-key",
            "dd_app_key": "test-app-key",
            "site_api_url": "https://api.datadoghq.com",
            "storage_tiers": [],  # Empty list
        }
        toolset.check_prerequisites()

        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error is not None
        assert "Failed to parse Datadog configuration" in toolset.error
        assert "storage_tiers" in toolset.error
        assert "at least 1 item" in toolset.error

    def test_check_prerequisites_integration(self):
        """Integration test to ensure check_prerequisites is called via CallablePrerequisite"""
        toolset = DatadogLogsToolset()

        # Verify the toolset has a CallablePrerequisite that calls prerequisites_callable
        assert len(toolset.prerequisites) == 1
        prerequisite = toolset.prerequisites[0]
        assert hasattr(prerequisite, "callable")
        assert prerequisite.callable == toolset.prerequisites_callable
