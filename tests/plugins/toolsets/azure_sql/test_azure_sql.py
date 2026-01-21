from unittest.mock import MagicMock, patch

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.azure_sql.azure_sql_toolset import AzureSQLToolset


class TestAzureSQLToolset:
    """Tests for Azure SQL toolset initialization and health checks."""

    def test_nominal_config_successful_initialization(self):
        """Test that toolset initializes correctly with valid configuration."""
        # Prepare valid configuration
        config = {
            "tenant_id": "test-tenant-id",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "database": {
                "subscription_id": "test-subscription-id",
                "resource_group": "test-resource-group",
                "server_name": "test-server",
                "database_name": "test-database",
            },
        }

        # Create mock tokens
        mock_token = MagicMock()
        mock_token.token = "test-token"

        # Create toolset instance
        toolset = AzureSQLToolset()

        # Mock Azure credential and token acquisition
        with patch(
            "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.ClientSecretCredential"
        ) as mock_credential_class:
            # Create mock credential instance
            mock_credential = MagicMock()
            mock_credential.get_token.return_value = mock_token
            mock_credential_class.return_value = mock_credential

            # Mock API client creation
            with patch(
                "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.AzureSQLAPIClient"
            ) as mock_api_client_class:
                mock_api_client = MagicMock()
                mock_api_client_class.return_value = mock_api_client

                # Set config and call check_prerequisites
                toolset.config = config
                toolset.check_prerequisites()

                # Verify credential was created with correct parameters
                mock_credential_class.assert_called_once_with(
                    tenant_id="test-tenant-id",
                    client_id="test-client-id",
                    client_secret="test-client-secret",
                )

                # Verify tokens were requested for both scopes
                assert mock_credential.get_token.call_count == 2
                mock_credential.get_token.assert_any_call(
                    "https://management.azure.com/.default"
                )
                mock_credential.get_token.assert_any_call(
                    "https://database.windows.net/.default"
                )

                # Verify API client was created
                mock_api_client_class.assert_called_once_with(
                    mock_credential, "test-subscription-id"
                )

                # Verify toolset status
                assert toolset.status == ToolsetStatusEnum.ENABLED
                assert toolset.error is None

                # Verify internal state
                assert toolset._api_client == mock_api_client
                assert toolset._database_config is not None
                assert (
                    toolset._database_config.subscription_id == "test-subscription-id"
                )
                assert toolset._database_config.resource_group == "test-resource-group"
                assert toolset._database_config.server_name == "test-server"
                assert toolset._database_config.database_name == "test-database"

    def test_incorrect_config_failed_health_check(self):
        """Test that toolset fails initialization with invalid configuration."""
        # Prepare invalid configuration (missing required fields)
        config = {
            "tenant_id": "test-tenant-id",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "database": {
                "subscription_id": "test-subscription-id",
                "resource_group": "test-resource-group",
                "server_name": "test-server",
                "database_name": "test-database",
            },
        }

        # Create toolset instance
        toolset = AzureSQLToolset()

        # Mock Azure credential to fail token acquisition
        with patch(
            "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.ClientSecretCredential"
        ) as mock_credential_class:
            # Create mock credential instance that fails to get token
            mock_credential = MagicMock()
            mock_credential.get_token.side_effect = Exception(
                "Authentication failed: Invalid client credentials"
            )
            mock_credential_class.return_value = mock_credential

            # Set config and call check_prerequisites
            toolset.config = config
            toolset.check_prerequisites()

            # Verify credential was created
            mock_credential_class.assert_called_once_with(
                tenant_id="test-tenant-id",
                client_id="test-client-id",
                client_secret="test-client-secret",
            )

            # Verify token acquisition was attempted
            mock_credential.get_token.assert_called_once_with(
                "https://management.azure.com/.default"
            )

            # Verify toolset status indicates failure
            assert toolset.status == ToolsetStatusEnum.FAILED
            assert toolset.error is not None
            assert "Failed to set up Azure authentication" in toolset.error
            assert "Authentication failed: Invalid client credentials" in toolset.error

            # Verify internal state is not set
            assert toolset._api_client is None
            assert toolset._database_config is None

    def test_missing_config_fails(self):
        """Test that toolset fails when no configuration is provided."""
        # Create toolset instance
        toolset = AzureSQLToolset()

        # Set empty config and call check_prerequisites
        toolset.config = {}
        toolset.check_prerequisites()

        # Verify toolset status indicates failure
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error is not None
        assert "missing" in toolset.error.lower()

    def test_default_credential_when_no_service_principal(self):
        """Test that DefaultAzureCredential is used when service principal credentials are not provided."""
        # Prepare configuration without service principal credentials
        config = {
            "tenant_id": None,
            "client_id": None,
            "client_secret": None,
            "database": {
                "subscription_id": "test-subscription-id",
                "resource_group": "test-resource-group",
                "server_name": "test-server",
                "database_name": "test-database",
            },
        }

        # Create mock tokens
        mock_token = MagicMock()
        mock_token.token = "test-token"

        # Create toolset instance
        toolset = AzureSQLToolset()

        # Mock DefaultAzureCredential
        with patch(
            "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.DefaultAzureCredential"
        ) as mock_default_credential_class:
            # Create mock credential instance
            mock_credential = MagicMock()
            mock_credential.get_token.return_value = mock_token
            mock_default_credential_class.return_value = mock_credential

            # Mock API client creation
            with patch(
                "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.AzureSQLAPIClient"
            ) as mock_api_client_class:
                mock_api_client = MagicMock()
                mock_api_client_class.return_value = mock_api_client

                # Set config and call check_prerequisites
                toolset.config = config
                toolset.check_prerequisites()

                # Verify DefaultAzureCredential was used
                mock_default_credential_class.assert_called_once_with()

                # Verify tokens were requested
                assert mock_credential.get_token.call_count == 2

                # Verify toolset status
                assert toolset.status == ToolsetStatusEnum.ENABLED
                assert toolset.error is None

    def test_sql_token_failure(self):
        """Test that toolset fails when SQL database token cannot be obtained."""
        # Prepare valid configuration
        config = {
            "tenant_id": "test-tenant-id",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "database": {
                "subscription_id": "test-subscription-id",
                "resource_group": "test-resource-group",
                "server_name": "test-server",
                "database_name": "test-database",
            },
        }

        # Create mock tokens
        mock_mgmt_token = MagicMock()
        mock_mgmt_token.token = "test-mgmt-token"

        # Create toolset instance
        toolset = AzureSQLToolset()

        # Mock Azure credential
        with patch(
            "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.ClientSecretCredential"
        ) as mock_credential_class:
            # Create mock credential instance that fails on second token request
            mock_credential = MagicMock()
            mock_credential.get_token.side_effect = [
                mock_mgmt_token,  # First call succeeds (management token)
                Exception("Failed to obtain SQL database token"),  # Second call fails
            ]
            mock_credential_class.return_value = mock_credential

            # Set config and call check_prerequisites
            toolset.config = config
            toolset.check_prerequisites()

            # Verify both token acquisitions were attempted
            assert mock_credential.get_token.call_count == 2

            # Verify toolset status indicates failure
            assert toolset.status == ToolsetStatusEnum.FAILED
            assert toolset.error is not None
            assert "Failed to set up Azure authentication" in toolset.error
            assert "Failed to obtain SQL database token" in toolset.error
