import os
import logging
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel
from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    StructuredToolResultStatus,
    Toolset,
    ToolsetTag,
)
from requests import RequestException  # type: ignore
from urllib.parse import urljoin

from holmes.plugins.toolsets.rabbitmq.api import (
    ClusterConnectionStatus,
    RabbitMQClusterConfig,
    get_cluster_status,
    make_request,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class RabbitMQConfig(BaseModel):
    clusters: List[RabbitMQClusterConfig]


class BaseRabbitMQTool(Tool):
    toolset: "RabbitMQToolset"

    def _get_cluster_config(self, cluster_id: Optional[str]) -> RabbitMQClusterConfig:
        if not self.toolset.config:
            raise ValueError("RabbitMQ is not configured.")
        cluster_ids = [c.id for c in self.toolset.config.clusters]
        if not cluster_id and len(cluster_ids) == 1:
            # cluster id is optional if there is only one configured
            return self.toolset.config.clusters[0]
        elif not cluster_id and len(cluster_ids) > 0:
            raise ValueError(
                f"No cluster is configured. Possible cluster_id values are: {', '.join(cluster_ids)}"
            )
        elif not cluster_id:
            raise ValueError("No cluster is configured")

        for cluster in self.toolset.config.clusters:
            if cluster.id == cluster_id:
                return cluster

        raise ValueError(
            f"Failed to find cluster_id={cluster_id} amongst configured clusters. Possible cluster_id values are: {', '.join(cluster_ids)}"
        )


class ListConfiguredClusters(BaseRabbitMQTool):
    def __init__(self, toolset: "RabbitMQToolset"):
        super().__init__(
            name="list_configured_clusters",
            description="List all configured clusters. Useful to get the id of a configured cluster (cluster_id) and pass as argument to other rabbitmq tool calls.",
            parameters={},
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config:
            raise ValueError("RabbitMQ is not configured.")

        available_clusters = [
            {
                "cluster_id": c.id,
                "management_url": c.management_url,
                "connection_status": c.connection_status,
            }
            for c in self.toolset.config.clusters
            if c.connection_status == ClusterConnectionStatus.SUCCESS
        ]
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=available_clusters
        )

    def get_parameterized_one_liner(self, params) -> str:
        return (
            f"{toolset_name_for_one_liner(self.toolset.name)}: List RabbitMQ Clusters"
        )


class GetRabbitMQClusterStatus(BaseRabbitMQTool):
    def __init__(self, toolset: "RabbitMQToolset"):
        super().__init__(
            name="get_rabbitmq_cluster_status",
            description="Fetches the overall status of the RabbitMQ cluster, including node information, listeners, and partition details. Crucial for detecting split-brain scenarios",
            parameters={
                "cluster_id": ToolParameter(
                    description="The id of the cluster obtained with list_configured_clusters. Only required if more than one rabbitmq cluster is configured.",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            # Fetch node details which include partition info
            cluster_config = self._get_cluster_config(
                cluster_id=params.get("cluster_id")
            )
            result = get_cluster_status(cluster_config)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS, data=result
            )

        except Exception as e:
            logging.info("Failed to process RabbitMQ cluster status", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error fetching RabbitMQ cluster status: {str(e)}",
                data=None,
            )

    def get_parameterized_one_liner(self, params) -> str:
        cluster_id = params.get("cluster_id", "")
        if cluster_id:
            return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Cluster Status ({cluster_id})"
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Cluster Status"


class RabbitMQToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="rabbitmq/core",
            description="Provides tools to interact with RabbitMQ to diagnose cluster health, node status, and specifically network partitions (split-brain).",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/rabbitmq/",
            icon_url="https://cdn.worldvectorlogo.com/logos/rabbitmq.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListConfiguredClusters(toolset=self),
                GetRabbitMQClusterStatus(toolset=self),
            ],
            tags=[ToolsetTag.CORE],
        )
        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        # Load instructions from the jinja2 template file
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "rabbitmq_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config or not config.get("clusters"):
            # Attempt to load from environment variables as fallback
            env_url = os.environ.get("RABBITMQ_MANAGEMENT_URL")
            env_user = os.environ.get("RABBITMQ_USERNAME", "guest")
            env_pass = os.environ.get("RABBITMQ_PASSWORD", "guest")
            if not env_url:
                return (
                    False,
                    "RabbitMQ toolset is misconfigured. 'management_url' is required.",
                )
            config = {
                "clusters": [
                    {
                        "id": "rabbitmq",
                        "management_url": env_url,
                        "username": env_user,
                        "password": env_pass,
                    }
                ]
            }
            logging.info("Loaded RabbitMQ config from environment variables.")

        try:
            self.config = RabbitMQConfig(**config)
        except Exception as e:
            return (False, f"Failed to parse RabbitMQ configuration: {str(e)}")

        return self._check_clusters_config(self.config)

    def _check_clusters_config(self, config: RabbitMQConfig) -> Tuple[bool, str]:
        errors = []
        for cluster_config in config.clusters:
            url = urljoin(cluster_config.management_url, "api/overview")

            try:
                data = make_request(
                    config=cluster_config,
                    method="GET",
                    url=url,
                )

                if data:
                    cluster_config.connection_status = ClusterConnectionStatus.SUCCESS
                    self._reload_llm_instructions()
                else:
                    error_message = f"Failed to connect to RabbitMQ Management API for cluster with id={cluster_config.id} at {url}. No data returned"
                    cluster_config.connection_status = ClusterConnectionStatus.ERROR
                    errors.append(error_message)

            except RequestException as e:
                error_message = f"Toolset failed health check for cluster with id={cluster_config.id} at {url} due to a failed http request. Connection error: {str(e)}"
                cluster_config.connection_status = ClusterConnectionStatus.ERROR
                errors.append(error_message)
            except Exception as e:
                error_message = f"Toolset failed health check for cluster with id={cluster_config.id} at {url}: {str(e)}"
                cluster_config.connection_status = ClusterConnectionStatus.ERROR
                errors.append(error_message)

        if errors:
            if len(errors) == 1:
                return (False, errors[0])
            else:
                return (False, "\n".join([f"- {error}" for error in errors]))
        else:
            return (True, "")

    def get_example_config(self):
        example_config = RabbitMQConfig(
            clusters=[
                RabbitMQClusterConfig(
                    management_url="http://<your-rabbitmq-server-or-service>:15672",
                    username="holmes_user",
                    password="holmes_password",
                )
            ]
        )
        return example_config.model_dump()
