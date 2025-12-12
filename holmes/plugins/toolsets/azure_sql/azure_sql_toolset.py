import os
import logging
from typing import Any, Dict, Tuple, Union

from azure.identity import DefaultAzureCredential, ClientSecretCredential

from holmes.core.tools import (
    CallablePrerequisite,
    ToolsetTag,
)
from holmes.plugins.toolsets.azure_sql.apis.azure_sql_api import AzureSQLAPIClient
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.azure_sql.azure_base_toolset import (
    BaseAzureSQLToolset,
    AzureSQLConfig,
    AzureSQLDatabaseConfig,
)

# Import all tool classes
from holmes.plugins.toolsets.azure_sql.tools.analyze_database_health_status import (
    AnalyzeDatabaseHealthStatus,
)
from holmes.plugins.toolsets.azure_sql.tools.analyze_database_performance import (
    AnalyzeDatabasePerformance,
)
from holmes.plugins.toolsets.azure_sql.tools.analyze_database_connections import (
    AnalyzeDatabaseConnections,
)
from holmes.plugins.toolsets.azure_sql.tools.analyze_database_storage import (
    AnalyzeDatabaseStorage,
)
from holmes.plugins.toolsets.azure_sql.tools.get_top_cpu_queries import GetTopCPUQueries
from holmes.plugins.toolsets.azure_sql.tools.get_slow_queries import GetSlowQueries
from holmes.plugins.toolsets.azure_sql.tools.get_top_data_io_queries import (
    GetTopDataIOQueries,
)
from holmes.plugins.toolsets.azure_sql.tools.get_top_log_io_queries import (
    GetTopLogIOQueries,
)
from holmes.plugins.toolsets.azure_sql.tools.get_active_alerts import GetActiveAlerts
from holmes.plugins.toolsets.azure_sql.tools.analyze_connection_failures import (
    AnalyzeConnectionFailures,
)


class AzureSQLToolset(BaseAzureSQLToolset):
    def __init__(self):
        # Reduce Azure SDK HTTP logging verbosity
        logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
            logging.WARNING
        )
        logging.getLogger("azure.identity").setLevel(logging.WARNING)
        logging.getLogger("azure.mgmt").setLevel(logging.WARNING)
        logging.getLogger("azure.monitor").setLevel(logging.WARNING)

        super().__init__(
            name="azure/sql",
            description="Analyzes Azure SQL Database performance, health, and operational issues using Azure REST APIs and Query Store data",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            docs_url="https://kagi.com/proxy/png-clipart-microsoft-sql-server-microsoft-azure-sql-database-microsoft-text-logo-thumbnail.png?c=4Sg1bvcUGOrhnDzXgoBBa0G0j27ykgskX4a8cLrZp_quzqlpVGVG02OqQtezTxy7lB6ydmTKgbVAn_F7BxofxK6LKKUZSpjJ1huIAsXPVaXyakO4sWXFiX0Wz_8WjkA0AIlO_oFfW31AKaj5RcvGcr3siy0n5kW-GcqdpeBWsmm_huxUT6RycULFCDFBwuUzHvVl5TW3cYqlMxT8ecPZfg%3D%3D",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/f/f7/Azure_SQL_Database_logo.svg/1200px-Azure_SQL_Database_logo.svg.png",
            tags=[ToolsetTag.CORE],
            tools=[
                AnalyzeDatabaseHealthStatus(self),
                AnalyzeDatabasePerformance(self),
                AnalyzeDatabaseConnections(self),
                AnalyzeDatabaseStorage(self),
                GetTopCPUQueries(self),
                GetSlowQueries(self),
                GetTopDataIOQueries(self),
                GetTopLogIOQueries(self),
                GetActiveAlerts(self),
                AnalyzeConnectionFailures(self),
            ],
        )
        self._reload_llm_instructions()

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR

        errors = []
        try:
            azure_sql_config = AzureSQLConfig(**config)

            # Set up Azure credentials
            try:
                credential: Union[ClientSecretCredential, DefaultAzureCredential]
                if (
                    azure_sql_config.tenant_id
                    and azure_sql_config.client_id
                    and azure_sql_config.client_secret
                ):
                    logging.info(
                        "Using ClientSecretCredential for Azure authentication"
                    )
                    credential = ClientSecretCredential(
                        tenant_id=azure_sql_config.tenant_id,
                        client_id=azure_sql_config.client_id,
                        client_secret=azure_sql_config.client_secret,
                    )
                else:
                    logging.info(
                        "Using DefaultAzureCredential for Azure authentication"
                    )
                    credential = DefaultAzureCredential()

                # Test the credential by attempting to get tokens for both required scopes
                mgmt_token = credential.get_token(
                    "https://management.azure.com/.default"
                )
                if not mgmt_token.token:
                    raise Exception("Failed to obtain Azure management token")

                # Test SQL database token as well
                sql_token = credential.get_token(
                    "https://database.windows.net/.default"
                )
                if not sql_token.token:
                    raise Exception("Failed to obtain Azure SQL database token")

            except Exception as e:
                message = f"Failed to set up Azure authentication: {str(e)}"
                logging.error(message)
                errors.append(message)
                return False, message

            # Store single database configuration and create API client
            self._database_config = azure_sql_config.database
            self._api_client = AzureSQLAPIClient(
                credential, azure_sql_config.database.subscription_id
            )
            logging.info(
                f"Configured Azure SQL database: {azure_sql_config.database.server_name}/{azure_sql_config.database.database_name}"
            )

            # Validate each tool's configuration requirements
            # tool_validation_errors = []
            # for tool in self.tools:
            #     if isinstance(tool, BaseAzureSQLTool):
            #         azure_tool = cast(BaseAzureSQLTool, tool)
            #         try:
            #             is_valid, error_msg = azure_tool.validate_config(
            #                 self._api_client, self._database_config
            #             )
            #             if not is_valid:
            #                 tool_validation_errors.append(
            #                     f"Tool '{azure_tool.name}' validation failed: {error_msg}"
            #                 )
            #         except Exception as e:
            #             tool_validation_errors.append(
            #                 f"Tool '{azure_tool.name}' validation error: {str(e)}"
            #             )

            # Combine all errors
            all_errors = errors  # + tool_validation_errors

            return len(all_errors) == 0, "\n".join(all_errors)
        except Exception as e:
            logging.exception("Failed to set up Azure SQL toolset")
            return False, str(e)

    def get_example_config(self) -> Dict[str, Any]:
        example_config = AzureSQLConfig(
            tenant_id="{{ env.AZURE_TENANT_ID }}",
            client_id="{{ env.AZURE_CLIENT_ID }}",
            client_secret="{{ env.AZURE_CLIENT_SECRET }}",
            database=AzureSQLDatabaseConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                resource_group="my-resource-group",
                server_name="myserver",
                database_name="mydatabase",
            ),
        )
        return example_config.model_dump()

    def _reload_llm_instructions(self):
        """Load Azure SQL specific troubleshooting instructions."""
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "azure_sql_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")
