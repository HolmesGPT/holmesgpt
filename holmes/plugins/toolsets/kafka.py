import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml  # type: ignore
from confluent_kafka.admin import (
    AdminClient,
    BrokerMetadata,
    ClusterMetadata,
    ConfigResource,
    ConsumerGroupDescription,
    GroupMember,
    GroupMetadata,
    KafkaError,
    ListConsumerGroupsResult,
    MemberAssignment,
    MemberDescription,
    PartitionMetadata,
    TopicMetadata,
)
from confluent_kafka import Consumer
from confluent_kafka._model import Node
from enum import Enum
from confluent_kafka.admin import _TopicPartition as TopicPartition
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
from holmes.plugins.toolsets.utils import get_param_or_raise, toolset_name_for_one_liner


class KafkaClusterConfig(BaseModel):
    name: str
    kafka_broker: str
    kafka_security_protocol: Optional[str] = None
    kafka_sasl_mechanism: Optional[str] = None
    kafka_username: Optional[str] = None
    kafka_password: Optional[str] = None
    kafka_client_id: Optional[str] = "holmes-kafka-client"


class KafkaConfig(BaseModel):
    kafka_clusters: List[KafkaClusterConfig]


def convert_to_dict(obj: Any) -> Union[str, Dict]:
    if isinstance(
        obj,
        (
            ClusterMetadata,
            BrokerMetadata,
            TopicMetadata,
            PartitionMetadata,
            GroupMember,
            GroupMetadata,
            ConsumerGroupDescription,
            MemberDescription,
            MemberAssignment,
        ),
    ):
        result = {}
        for key, value in vars(obj).items():
            if value is not None and value != -1 and value != []:
                if isinstance(value, dict):
                    result[key] = {k: convert_to_dict(v) for k, v in value.items()}
                elif isinstance(value, list):
                    result[key] = [convert_to_dict(item) for item in value]  # type: ignore
                else:
                    result[key] = convert_to_dict(value)  # type: ignore
        return result
    if isinstance(obj, TopicPartition):
        return str(obj)
    if isinstance(obj, KafkaError):
        return str(obj)
    if isinstance(obj, Node):
        # Convert Node to a simple dict
        return {"host": obj.host, "id": obj.id, "port": obj.port}
    if isinstance(obj, Enum):
        # Convert enum to its string representation
        return str(obj).split(".")[-1]  # Get just the enum value name
    return obj


def format_list_consumer_group_errors(errors: Optional[List]) -> str:
    errors_text = ""
    if errors:
        if len(errors) > 1:
            errors_text = "# Some errors happened while listing consumer groups:\n\n"
        errors_text = errors_text + "\n\n".join(
            [f"## Error:\n{str(error)}" for error in errors]
        )

    return errors_text


class BaseKafkaTool(Tool):
    toolset: "KafkaToolset"

    def get_kafka_client(self, cluster_name: Optional[str]) -> AdminClient:
        """
        Retrieves the correct Kafka AdminClient based on the cluster name.
        """
        if len(self.toolset.clients) == 1:
            return next(
                iter(self.toolset.clients.values())
            )  # Return the only available client

        if not cluster_name:
            raise Exception("Missing cluster name to resolve Kafka client")

        if cluster_name in self.toolset.clients:
            return self.toolset.clients[cluster_name]

        raise Exception(
            f"Failed to resolve Kafka client. No matching cluster: {cluster_name}"
        )

    def get_bootstrap_servers(self, cluster_name: str) -> str:
        """
        Retrieves the bootstrap servers for a given cluster.
        """
        if not self.toolset.kafka_config:
            raise Exception("Kafka configuration not available")

        for cluster in self.toolset.kafka_config.kafka_clusters:
            if cluster.name == cluster_name:
                return cluster.kafka_broker

        raise Exception(
            f"Failed to resolve bootstrap servers. No matching cluster: {cluster_name}"
        )


class ListKafkaConsumers(BaseKafkaTool):
    def __init__(self, toolset: "KafkaToolset"):
        super().__init__(
            name="list_kafka_consumers",
            description="Lists all Kafka consumer groups in the cluster",
            parameters={
                "kafka_cluster_name": ToolParameter(
                    description="The name of the kafka cluster to investigate",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            kafka_cluster_name = get_param_or_raise(params, "kafka_cluster_name")
            client = self.get_kafka_client(kafka_cluster_name)
            if client is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="No admin_client on toolset. This toolset is misconfigured.",
                    params=params,
                )

            futures = client.list_consumer_groups()
            list_groups_result: ListConsumerGroupsResult = futures.result()
            groups_text = ""
            if list_groups_result.valid and len(list_groups_result.valid) > 0:
                groups = []
                for group in list_groups_result.valid:
                    groups.append(
                        {
                            "group_id": group.group_id,
                            "is_simple_consumer_group": group.is_simple_consumer_group,
                            "state": str(group.state),
                            "type": str(group.type),
                        }
                    )
                groups_text = yaml.dump({"consumer_groups": groups})
            else:
                groups_text = "No consumer group was found"

            errors_text = format_list_consumer_group_errors(list_groups_result.errors)

            result_text = groups_text
            if errors_text:
                result_text = result_text + "\n\n" + errors_text
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result_text,
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to list consumer groups: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        cluster = params.get("kafka_cluster_name", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Consumer Groups ({cluster})"


class DescribeConsumerGroup(BaseKafkaTool):
    def __init__(self, toolset: "KafkaToolset"):
        super().__init__(
            name="describe_consumer_group",
            description="Describes a specific Kafka consumer group",
            parameters={
                "kafka_cluster_name": ToolParameter(
                    description="The name of the kafka cluster to investigate",
                    type="string",
                    required=True,
                ),
                "group_id": ToolParameter(
                    description="The ID of the consumer group to describe",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        group_id = params["group_id"]
        try:
            kafka_cluster_name = get_param_or_raise(params, "kafka_cluster_name")
            client = self.get_kafka_client(kafka_cluster_name)
            if client is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="No admin_client on toolset. This toolset is misconfigured.",
                    params=params,
                )

            futures = client.describe_consumer_groups([group_id])

            if futures.get(group_id):
                group_metadata = futures.get(group_id).result()
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=yaml.dump(convert_to_dict(group_metadata)),
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Group not found",
                    params=params,
                )
        except Exception as e:
            error_msg = f"Failed to describe consumer group {group_id}: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        group_id = params.get("group_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Describe Consumer Group ({group_id})"


class ListTopics(BaseKafkaTool):
    def __init__(self, toolset: "KafkaToolset"):
        super().__init__(
            name="list_topics",
            description="Lists all Kafka topics in the cluster",
            parameters={
                "kafka_cluster_name": ToolParameter(
                    description="The name of the kafka cluster to investigate",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            kafka_cluster_name = get_param_or_raise(params, "kafka_cluster_name")
            client = self.get_kafka_client(kafka_cluster_name)
            if client is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="No admin_client on toolset. This toolset is misconfigured.",
                    params=params,
                )

            topics = client.list_topics()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=yaml.dump(convert_to_dict(topics)),
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to list topics: {str(e)}"
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        cluster = params.get("kafka_cluster_name", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Kafka Topics ({cluster})"


class DescribeTopic(BaseKafkaTool):
    def __init__(self, toolset: "KafkaToolset"):
        super().__init__(
            name="describe_topic",
            description="Describes details of a specific Kafka topic",
            parameters={
                "kafka_cluster_name": ToolParameter(
                    description="The name of the kafka cluster to investigate",
                    type="string",
                    required=True,
                ),
                "topic_name": ToolParameter(
                    description="The name of the topic to describe",
                    type="string",
                    required=True,
                ),
                "fetch_configuration": ToolParameter(
                    description="If true, also fetches the topic configuration. defaults to false",
                    type="boolean",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        topic_name = params["topic_name"]
        try:
            kafka_cluster_name = get_param_or_raise(params, "kafka_cluster_name")
            client = self.get_kafka_client(kafka_cluster_name)
            if client is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="No admin_client on toolset. This toolset is misconfigured.",
                    params=params,
                )
            config_future = None
            if str(params.get("fetch_configuration", False)).lower() == "true":
                resource = ConfigResource("topic", topic_name)
                configs = client.describe_configs([resource])
                config_future = next(iter(configs.values()))

            metadata = client.list_topics(topic_name).topics[topic_name]

            metadata = convert_to_dict(metadata)
            result: dict = {"metadata": metadata}

            if config_future:
                config = config_future.result()
                result["configuration"] = convert_to_dict(config)

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=yaml.dump(result),
                params=params,
            )
        except Exception as e:
            error_msg = f"Failed to describe topic {topic_name}: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        topic = params.get("topic_name", "")
        return (
            f"{toolset_name_for_one_liner(self.toolset.name)}: Describe Topic ({topic})"
        )


def group_has_topic(
    client: AdminClient,
    consumer_group_description: ConsumerGroupDescription,
    topic_name: str,
    bootstrap_servers: str,
    topic_metadata: Any,
):
    # Check active member assignments
    for member in consumer_group_description.members:
        for topic_partition in member.assignment.topic_partitions:
            if topic_partition.topic == topic_name:
                return True

    # Check committed offsets for the topic (handles inactive/empty consumer groups)
    try:
        # Try using the Consumer class to check committed offsets for the specific group

        # Create a consumer with the same group.id as the one we're checking
        # This allows us to check its committed offsets
        consumer_config = {
            "bootstrap.servers": bootstrap_servers,
            "group.id": consumer_group_description.group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # Don't auto-commit to avoid side effects
        }
        consumer = Consumer(consumer_config)

        # Check topic metadata to know which partitions exist
        if topic_name not in topic_metadata.topics:
            consumer.close()
            return False

        # Create TopicPartition objects for all partitions of the topic
        topic_partitions = []
        for partition_id in topic_metadata.topics[topic_name].partitions:
            topic_partitions.append(TopicPartition(topic_name, partition_id))

        # Check committed offsets for this consumer group on these topic partitions

        committed_offsets = consumer.committed(topic_partitions, timeout=10.0)
        consumer.close()

        # Check if any partition has a valid committed offset
        for tp in committed_offsets:
            if tp.offset != -1001:  # -1001 means no committed offset
                return True

        return False

    except Exception:
        # If we can't check offsets, fall back to just the active assignment check
        pass

    return False


class FindConsumerGroupsByTopic(BaseKafkaTool):
    def __init__(self, toolset: "KafkaToolset"):
        super().__init__(
            name="find_consumer_groups_by_topic",
            description="Finds all consumer groups consuming from a specific topic",
            parameters={
                "kafka_cluster_name": ToolParameter(
                    description="The name of the kafka cluster to investigate",
                    type="string",
                    required=True,
                ),
                "topic_name": ToolParameter(
                    description="The name of the topic to find consumers for",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        topic_name = params["topic_name"]
        try:
            kafka_cluster_name = get_param_or_raise(params, "kafka_cluster_name")
            client = self.get_kafka_client(kafka_cluster_name)
            if client is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="No admin_client on toolset. This toolset is misconfigured.",
                    params=params,
                )

            groups_future = client.list_consumer_groups()
            groups: ListConsumerGroupsResult = groups_future.result()

            consumer_groups = []
            group_ids_to_evaluate: list[str] = []
            if groups.valid:
                group_ids_to_evaluate = group_ids_to_evaluate + [
                    group.group_id for group in groups.valid
                ]

            if len(group_ids_to_evaluate) > 0:
                consumer_groups_futures = client.describe_consumer_groups(
                    group_ids_to_evaluate
                )

                for (
                    group_id,
                    consumer_group_description_future,
                ) in consumer_groups_futures.items():
                    consumer_group_description = (
                        consumer_group_description_future.result()
                    )
                    bootstrap_servers = self.get_bootstrap_servers(kafka_cluster_name)
                    topic_metadata = client.list_topics(topic_name, timeout=10)
                    if group_has_topic(
                        client=client,
                        consumer_group_description=consumer_group_description,
                        topic_name=topic_name,
                        bootstrap_servers=bootstrap_servers,
                        topic_metadata=topic_metadata,
                    ):
                        consumer_groups.append(
                            convert_to_dict(consumer_group_description)
                        )

            errors_text = format_list_consumer_group_errors(groups.errors)

            result_text = None
            if len(consumer_groups) > 0:
                result_text = yaml.dump(consumer_groups)
            else:
                result_text = f"No consumer group were found for topic {topic_name}"

            if errors_text:
                result_text = result_text + "\n\n" + errors_text

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result_text,
                params=params,
            )
        except Exception as e:
            error_msg = (
                f"Failed to find consumer groups for topic {topic_name}: {str(e)}"
            )
            logging.error(error_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        topic = params.get("topic_name", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Find Topic Consumers ({topic})"


class ListKafkaClusters(BaseKafkaTool):
    def __init__(self, toolset: "KafkaToolset"):
        super().__init__(
            name="list_kafka_clusters",
            description="Lists all available Kafka clusters configured in HolmesGPT",
            parameters={},
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        cluster_names = list(self.toolset.clients.keys())
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="Available Kafka Clusters:\n" + "\n".join(cluster_names),
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List Kafka Clusters"


class KafkaToolset(Toolset):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    clients: Dict[str, AdminClient] = {}
    kafka_config: Optional[KafkaConfig] = None

    def __init__(self):
        super().__init__(
            name="kafka/admin",
            description="Fetches metadata from multiple Kafka clusters",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/kafka/",
            icon_url="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcT-cR1JrBgJxB_SPVKUIRwtiHnR8qBvLeHXjQ&s",
            tags=[ToolsetTag.CORE],
            tools=[
                ListKafkaClusters(self),
                ListKafkaConsumers(self),
                DescribeConsumerGroup(self),
                ListTopics(self),
                DescribeTopic(self),
                FindConsumerGroupsByTopic(self),
            ],
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR
        errors = []
        try:
            kafka_config = KafkaConfig(**config)
            self.kafka_config = kafka_config

            for cluster in kafka_config.kafka_clusters:
                try:
                    logging.info(f"Setting up Kafka client for cluster: {cluster.name}")
                    admin_config = {
                        "bootstrap.servers": cluster.kafka_broker,
                        "client.id": cluster.kafka_client_id,
                    }

                    if cluster.kafka_security_protocol:
                        admin_config["security.protocol"] = (
                            cluster.kafka_security_protocol
                        )
                    if cluster.kafka_sasl_mechanism:
                        admin_config["sasl.mechanisms"] = cluster.kafka_sasl_mechanism
                    if cluster.kafka_username and cluster.kafka_password:
                        admin_config["sasl.username"] = cluster.kafka_username
                        admin_config["sasl.password"] = cluster.kafka_password

                    client = AdminClient(admin_config)
                    self.clients[cluster.name] = client  # Store in dictionary
                except Exception as e:
                    message = (
                        f"Failed to set up Kafka client for {cluster.name}: {str(e)}"
                    )
                    logging.error(message)
                    errors.append(message)

            return len(self.clients) > 0, "\n".join(errors)
        except Exception as e:
            logging.exception("Failed to set up Kafka toolset")
            return False, str(e)

    def get_example_config(self) -> Dict[str, Any]:
        example_config = KafkaConfig(
            kafka_clusters=[
                KafkaClusterConfig(
                    name="us-west-kafka",
                    kafka_broker="broker1.example.com:9092,broker2.example.com:9092",
                    kafka_security_protocol="SASL_SSL",
                    kafka_sasl_mechanism="PLAIN",
                    kafka_username="{{ env.KAFKA_USERNAME }}",
                    kafka_password="{{ env.KAFKA_PASSWORD }}",
                ),
                KafkaClusterConfig(
                    name="eu-central-kafka",
                    kafka_broker="broker3.example.com:9092",
                    kafka_security_protocol="SSL",
                ),
            ]
        )
        return example_config.model_dump()
