import logging
from typing import Any, Dict, List, Optional, Tuple

from opensearchpy import OpenSearch
from pydantic import BaseModel, ConfigDict

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
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class OpenSearchHttpAuth(BaseModel):
    username: str
    password: str


class OpenSearchHost(BaseModel):
    host: str
    port: int = 9200


class OpenSearchCluster(BaseModel):
    hosts: list[OpenSearchHost]
    headers: Optional[dict[str, Any]] = None
    use_ssl: bool = True
    ssl_assert_hostname: bool = False
    verify_certs: bool = False
    ssl_show_warn: bool = False
    http_auth: Optional[OpenSearchHttpAuth] = None


class OpenSearchConfig(BaseModel):
    opensearch_clusters: list[OpenSearchCluster]


class OpenSearchClient:
    def __init__(self, **kwargs):
        # Handle http_auth explicitly
        if "http_auth" in kwargs:
            http_auth = kwargs.pop("http_auth")
            if isinstance(http_auth, dict):
                kwargs["http_auth"] = (
                    http_auth.get("username"),
                    http_auth.get("password"),
                )
        # Initialize OpenSearch client
        self.hosts = [host.get("host") for host in kwargs.get("hosts", [])]
        self.client = OpenSearch(**kwargs)


def get_client(clients: List[OpenSearchClient], host: Optional[str]):
    if len(clients) == 1:
        return clients[0]

    if not host:
        raise Exception("Missing host to resolve opensearch client")

    for client in clients:
        found = any(host in client.hosts for client in clients)
        if found:
            return client

    raise Exception(
        f"Failed to resolve opensearch client. Could not find a matching host: {host}"
    )


class BaseOpenSearchTool(Tool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    toolset: "OpenSearchToolset"


class ListShards(BaseOpenSearchTool):
    def __init__(self, toolset: "OpenSearchToolset"):
        super().__init__(
            name="opensearch_list_shards",
            description="List the shards within an opensearch cluster",
            parameters={
                "host": ToolParameter(
                    description="The cluster host",
                    type="string",
                    required=True,
                )
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client = get_client(self.toolset.clients, host=params.get("host", ""))
        shards = client.client.cat.shards()
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=str(shards),
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        host = params.get("host", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Shards ({host})"


class GetClusterSettings(BaseOpenSearchTool):
    def __init__(self, toolset: "OpenSearchToolset"):
        super().__init__(
            name="opensearch_get_cluster_settings",
            description="Retrieve the cluster's settings",
            parameters={
                "host": ToolParameter(
                    description="The cluster host",
                    type="string",
                    required=True,
                )
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client = get_client(self.toolset.clients, host=params.get("host"))
        response = client.client.cluster.get_settings(
            include_defaults=True, flat_settings=True
        )
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=str(response),
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        host = params.get("host", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Cluster Settings ({host})"


class GetClusterHealth(BaseOpenSearchTool):
    def __init__(self, toolset: "OpenSearchToolset"):
        super().__init__(
            name="opensearch_get_cluster_health",
            description="Retrieve the cluster's health",
            parameters={
                "host": ToolParameter(
                    description="The cluster host",
                    type="string",
                    required=True,
                )
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client = get_client(self.toolset.clients, host=params.get("host", ""))
        health = client.client.cluster.health()
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=str(health),
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        host = params.get("host", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Check Cluster Health ({host})"


class ListOpenSearchHosts(BaseOpenSearchTool):
    def __init__(self, toolset: "OpenSearchToolset"):
        super().__init__(
            name="opensearch_list_hosts",
            description="List all OpenSearch hosts in the cluster",
            parameters={},
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        hosts = [host for client in self.toolset.clients for host in client.hosts]
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=str(hosts),
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List OpenSearch Hosts"


class OpenSearchToolset(Toolset):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    clients: List[OpenSearchClient] = []

    def __init__(self):
        super().__init__(
            name="opensearch/status",
            enabled=False,
            description="Provide cluster metadata information like health, shards, settings.",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/opensearch-status/",
            icon_url="https://opensearch.org/assets/brand/PNG/Mark/opensearch_mark_default.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListShards(self),
                GetClusterSettings(self),
                GetClusterHealth(self),
                ListOpenSearchHosts(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR

        try:
            os_config = OpenSearchConfig(**config)
            errors = []
            for cluster in os_config.opensearch_clusters:
                try:
                    logging.info("Setting up OpenSearch client")
                    cluster_kwargs = cluster.model_dump()
                    client = OpenSearchClient(**cluster_kwargs)
                    if client.client.cluster.health(params={"timeout": 5}):
                        self.clients.append(client)
                except Exception as e:
                    message = f"Failed to set up opensearch client {str(cluster.hosts)}. {str(e)}"
                    logging.exception(message)
                    errors.append(message)

            return len(self.clients) > 0, "\n".join(errors)
        except Exception as e:
            logging.exception("Failed to set up OpenSearch toolset")
            return False, str(e)

    def get_example_config(self) -> Dict[str, Any]:
        example_config = OpenSearchConfig(
            opensearch_clusters=[
                OpenSearchCluster(
                    hosts=[OpenSearchHost(host="YOUR OPENSEACH HOST")],
                    headers={"Authorization": "{{ env.OPENSEARCH_BEARER_TOKEN }}"},
                    use_ssl=True,
                    ssl_assert_hostname=False,
                )
            ]
        )
        return example_config.model_dump()
