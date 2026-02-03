from typing import ClassVar, Optional, Tuple, Type

from pydantic import ConfigDict, Field

from holmes.core.tools import Tool, Toolset
from holmes.plugins.toolsets.azure_sql.apis.azure_sql_api import AzureSQLAPIClient
from holmes.utils.pydantic_utils import ToolsetConfig


class AzureSQLDatabaseConfig(ToolsetConfig):
    subscription_id: str = Field(
        title="Subscription ID",
        description="Azure subscription ID",
        examples=["12345678-1234-1234-1234-123456789012"],
    )
    resource_group: str = Field(
        title="Resource Group",
        description="Azure resource group name",
        examples=["my-resource-group"],
    )
    server_name: str = Field(
        title="Server Name",
        description="Azure SQL server name",
        examples=["myserver"],
    )
    database_name: str = Field(
        title="Database Name",
        description="Azure SQL database name",
        examples=["mydatabase"],
    )


class AzureSQLConfig(ToolsetConfig):
    database: AzureSQLDatabaseConfig = Field(
        title="Database",
        description="Azure SQL database connection details",
    )
    tenant_id: Optional[str] = Field(
        default=None,
        title="Tenant ID",
        description="Azure AD tenant ID (required for service principal auth)",
        examples=["{{ env.AZURE_TENANT_ID }}"],
    )
    client_id: Optional[str] = Field(
        default=None,
        title="Client ID",
        description="Azure AD client/application ID (required for service principal auth)",
        examples=["{{ env.AZURE_CLIENT_ID }}"],
    )
    client_secret: Optional[str] = Field(
        default=None,
        title="Client Secret",
        description="Azure AD client secret (required for service principal auth)",
        examples=["{{ env.AZURE_CLIENT_SECRET }}"],
    )


class BaseAzureSQLToolset(Toolset):
    config_classes: ClassVar[list[Type[AzureSQLConfig]]] = [AzureSQLConfig]

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _api_client: Optional[AzureSQLAPIClient] = None
    _database_config: Optional[AzureSQLDatabaseConfig] = None

    def api_client(self):
        if not self._api_client:
            raise Exception(
                "Toolset is missing api_client. This is likely a code issue and not a configuration issue"
            )
        else:
            return self._api_client

    def database_config(self):
        if not self._database_config:
            raise Exception(
                "Toolset is missing database_config. This is likely a code issue and not a configuration issue"
            )
        else:
            return self._database_config


class BaseAzureSQLTool(Tool):
    toolset: BaseAzureSQLToolset

    @staticmethod
    def validate_config(
        api_client: AzureSQLAPIClient, database_config: AzureSQLDatabaseConfig
    ) -> Tuple[bool, str]:
        # Each tool is able to validate whether it can work and generate output with this config.
        # The tool should report an error if a permission is missing. e.g. return False, "The client '597a70b9-9f01-4739-ac3e-ac8a934e9ffc' with object id '597a70b9-9f01-4739-ac3e-ac8a934e9ffc' does not have authorization to perform action 'Microsoft.Insights/metricAlerts/read' over scope '/subscriptions/e7a7e3c5-ff48-4ccb-898b-83aa5d2f9097/resourceGroups/arik-aks-dev_group/providers/Microsoft.Insights' or the scope is invalid."
        # The tool should return multiple errors in the return message if there are multiple issues that prevent it from fully working
        return True, ""
