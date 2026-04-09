import atexit
import base64
import logging
import os
import tempfile
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml  # type: ignore
from confluent_kafka import Consumer
from confluent_kafka._model import Node
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
from confluent_kafka.admin import _TopicPartition as TopicPartition
from pydantic import ConfigDict, Field

from holmes.core.tools import (
    CallablePrerequisite,
    ClassVar,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
    Type,
)
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.utils import get_param_or_raise, toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig, build_config_example


class KafkaClusterConfig(ToolsetConfig):
    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "kafka_broker": "broker",
        "kafka_security_protocol": "security_protocol",
        "kafka_sasl_mechanism": "sasl_mechanism",
        "kafka_client_id": "client_id",
        "kafka_username": "username",
        "kafka_password": "password",
    }

    name: str = Field(
        title="Name",
        description="Name identifier for this Kafka cluster",
        examples=["us-west-kafka", "eu-central-kafka"],
    )
    broker: str = Field(
        title="Broker Address",
        description="Kafka broker address",
        examples=[
            "broker1.example.com:9092,broker2.example.com:9092",
            "broker3.example.com:9092",
            "kafka.default.svc:9092",
        ],
    )
    security_protocol: Optional[str] = Field(
        default=None,
        title="Security Protocol",
        description="Security protocol (e.g., PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL)",
        examples=["SASL_SSL", "SSL", "PLAINTEXT"],
    )
    sasl_mechanism: Optional[str] = Field(
        default=None,
        title="SASL Mechanism",
        description="SASL mechanism (e.g., PLAIN, SCRAM-SHA-256, SCRAM-SHA-512)",
        examples=["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"],
    )
    username: Optional[str] = Field(
        default=None,
        title="Username",
        description="Username for SASL authentication",
        examples=["{{ env.KAFKA_USERNAME }}"],
    )
    password: Optional[str] = Field(
        default=None,
        title="Password",
        description="Password for SASL authentication",
        examples=["{{ env.KAFKA_PASSWORD }}"],
    )
    client_id: Optional[str] = Field(
        default="holmes-kafka-client",
        title="Client ID",
        description="Client ID for Kafka connections",
    )

    # --- mTLS / SSL fields (mirrors kafka-mcp-server KAFKA_TLS_* env vars) ---
    ssl_ca_cert_path: Optional[str] = Field(
        default=None,
        title="CA Certificate Path",
        description=(
            "Path to the CA certificate file (PEM format) for broker verification. "
            "Use this when certs are mounted as Kubernetes secrets. "
            "Equivalent to KAFKA_TLS_CA_CERT_FILE in kafka-mcp-server."
        ),
        examples=["/etc/kafka-tls/kafka_dellca2018-bundle.crt"],
    )
    ssl_client_cert_path: Optional[str] = Field(
        default=None,
        title="Client Certificate Path",
        description=(
            "Path to the client certificate file (PEM format) for mTLS. "
            "Equivalent to KAFKA_TLS_CERT_FILE in kafka-mcp-server."
        ),
        examples=["/etc/kafka-tls/kafka_certificate.pem"],
    )
    ssl_client_key_path: Optional[str] = Field(
        default=None,
        title="Client Key Path",
        description=(
            "Path to the client private key file (PEM format) for mTLS. "
            "Equivalent to KAFKA_TLS_KEY_FILE in kafka-mcp-server."
        ),
        examples=["/etc/kafka-tls/kafka_private_key.pem"],
    )
    ssl_ca_cert: Optional[str] = Field(
        default=None,
        title="CA Certificate (base64)",
        description=(
            "Base64-encoded CA certificate (PEM format). "
            "Alternative to ssl_ca_cert_path when certs are passed inline."
        ),
        examples=["{{ env.KAFKA_CA_CERT_BASE64 }}"],
    )
    ssl_client_cert: Optional[str] = Field(
        default=None,
        title="Client Certificate (base64)",
        description=(
            "Base64-encoded client certificate (PEM format) for mTLS. "
            "Alternative to ssl_client_cert_path."
        ),
        examples=["{{ env.KAFKA_CLIENT_CERT_BASE64 }}"],
    )
    ssl_client_key: Optional[str] = Field(
        default=None,
        title="Client Key (base64)",
        description=(
            "Base64-encoded client private key (PEM format) for mTLS. "
            "Alternative to ssl_client_key_path."
        ),
        examples=["{{ env.KAFKA_CLIENT_KEY_BASE64 }}"],
    )


def _build_ssl_config(
    cluster: "KafkaClusterConfig",
) -> Tuple[Dict[str, str], List[str]]:
    """Build the confluent-kafka SSL property dict for *cluster*.

    Supports two sources for certificates (same precedence order as
    kafka-mcp-server's environment-variable approach):

    1. File-path fields (``ssl_*_path``) — preferred for Kubernetes mounted
       secrets; no temporary files created.
    2. Base64-encoded inline fields (``ssl_*``) — decoded and written to
       secure temporary files.  The returned ``temp_files`` list contains
       the paths; callers should register them for cleanup (e.g. via
       ``atexit``) because the file paths are also stored in ``ssl_configs``
       for later reuse by Consumer creation in ``group_has_topic()``.

    Returns
    -------
    ssl_config : dict
        Ready-to-merge dict for an ``AdminClient`` or ``Consumer`` config.
    temp_files : list[str]
        Paths to any temporary files created for base64-decoded certs.
    """
    ssl_config: Dict[str, str] = {}
    temp_files: List[str] = []

    def _resolve_cert(
        path_val: Optional[str], b64_val: Optional[str], label: str
    ) -> Optional[str]:
        """Return file path: prefer explicit path, fall back to base64 temp file."""
        if path_val:
            if not os.path.isfile(path_val):
                raise FileNotFoundError(
                    f"Kafka SSL {label} file not found: {path_val}"
                )
            return path_val
        if b64_val:
            try:
                data = base64.b64decode(b64_val, validate=True)
            except Exception as exc:
                raise ValueError(
                    f"Kafka SSL {label}: failed to base64-decode value: {exc}"
                ) from exc
            # Create the file atomically with restrictive permissions.
            # mkstemp() already produces a 0o600 file on most Unix systems.
            fd, tmp_path = tempfile.mkstemp(
                suffix=".pem", prefix=f"holmes_kafka_{label}_"
            )
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            temp_files.append(tmp_path)
            return tmp_path
        return None

    def _cleanup_temp_files() -> None:
        for tmp in temp_files:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    try:
        ca_path = _resolve_cert(cluster.ssl_ca_cert_path, cluster.ssl_ca_cert, "ca_cert")
        cert_path = _resolve_cert(
            cluster.ssl_client_cert_path, cluster.ssl_client_cert, "client_cert"
        )
        key_path = _resolve_cert(
            cluster.ssl_client_key_path, cluster.ssl_client_key, "client_key"
        )
    except Exception:
        _cleanup_temp_files()
        raise

    # cert and key must be provided as a pair
    if bool(cert_path) != bool(key_path):
        _cleanup_temp_files()
        raise ValueError(
            f"Kafka SSL for cluster '{cluster.name}': "
            "ssl_client_cert* and ssl_client_key* must both be provided or both omitted"
        )

    if ca_path:
        ssl_config["ssl.ca.location"] = ca_path
    if cert_path:
        ssl_config["ssl.certificate.location"] = cert_path
    if key_path:
        ssl_config["ssl.key.location"] = key_path

    # Auto-set security.protocol to SSL when cert material is present and the
    # user has not already configured a protocol (e.g. SASL_SSL).
    if ssl_config and not cluster.security_protocol:
        ssl_config["security.protocol"] = "SSL"

    return ssl_config, temp_files


class KafkaConfig(ToolsetConfig):
    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "kafka_clusters": "clusters",
    }

    clusters: List[KafkaClusterConfig] = Field(
        title="Clusters",
        description="List of Kafka clusters to connect to",
        examples=[[build_config_example(KafkaClusterConfig)]],
    )


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

        for cluster in self.toolset.kafka_config.clusters:
            if cluster.name == cluster_name:
                return cluster.broker

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
            groups = []
            if list_groups_result.valid:
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

            futures = client.describe_consumer_groups(
                [group_id], request_timeout=10
            )

            if futures.get(group_id):
                group_metadata = futures.get(group_id).result(timeout=15)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=convert_to_dict(group_metadata),
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
                data=convert_to_dict(topics),
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
                data=result,
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
    kafka_cluster_name: str,
    ssl_config: Optional[Dict[str, str]] = None,
):
    """Return True if *consumer_group_description* is actively consuming *topic_name*.

    Checks two places in order:
    1. Active member assignments — fast, no network round-trip beyond the admin call.
    2. Committed offsets via a temporary Consumer — catches inactive/empty groups
       that still have committed offsets for the topic.

    Parameters
    ----------
    client:
        AdminClient used to fetch committed offsets.
    consumer_group_description:
        Group description returned by ``describe_consumer_groups``.
    topic_name:
        The topic to check for.
    bootstrap_servers:
        Comma-separated broker list, forwarded to the temporary Consumer.
    topic_metadata:
        Cluster metadata (from ``list_topics``) used to enumerate topic partitions.
    kafka_cluster_name:
        Logical cluster name, used in log messages for diagnostics.
    ssl_config:
        SSL/mTLS and SASL auth properties to merge into the Consumer config so it
        can connect to a TLS- or SASL-protected broker.  Should include all
        auth-related keys (``security.protocol``, ``sasl.*``, ``ssl.*``) that
        were used to build the AdminClient for this cluster.
    """
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
        if ssl_config:
            consumer_config.update(ssl_config)
        consumer = Consumer(consumer_config)

        try:
            # Check topic metadata to know which partitions exist
            if topic_name not in topic_metadata.topics:
                return False

            # Create TopicPartition objects for all partitions of the topic
            topic_partitions = []
            for partition_id in topic_metadata.topics[topic_name].partitions:
                topic_partitions.append(TopicPartition(topic_name, partition_id))

            # Check committed offsets for this consumer group on these topic partitions

            committed_offsets = consumer.committed(topic_partitions, timeout=10.0)

            # Check if any partition has a valid committed offset
            for tp in committed_offsets:
                if tp.offset != -1001:  # -1001 means no committed offset
                    return True

            return False
        finally:
            consumer.close()

    except Exception:
        # If we can't check offsets, fall back to just the active assignment check
        logging.warning(
            f"group_has_topic: failed to check committed offsets for group "
            f"{consumer_group_description.group_id!r} on topic {topic_name!r} "
            f"in cluster {kafka_cluster_name!r}; falling back to active assignment check",
            exc_info=True,
        )

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

            # Early exit: if the topic doesn't exist there can't be any consumers
            topic_metadata = client.list_topics(topic_name, timeout=10)
            topic_meta = topic_metadata.topics.get(topic_name)
            if topic_meta is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=f"No consumer group were found for topic {topic_name}",
                    params=params,
                )
            if topic_meta.error is not None:
                if topic_meta.error.code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS,
                        data=f"No consumer group were found for topic {topic_name}",
                        params=params,
                    )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=(
                        f"Error looking up topic {topic_name!r} on cluster {kafka_cluster_name!r}"
                        f" via list_topics(topic={topic_name!r}, timeout=10): {topic_meta.error}"
                    ),
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
                    if group_has_topic(
                        client=client,
                        consumer_group_description=consumer_group_description,
                        topic_name=topic_name,
                        bootstrap_servers=bootstrap_servers,
                        topic_metadata=topic_metadata,
                        kafka_cluster_name=kafka_cluster_name,
                        ssl_config=self.toolset.ssl_configs.get(kafka_cluster_name),
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
    config_classes: ClassVar[list[Type[KafkaConfig]]] = [KafkaConfig]

    model_config = ConfigDict(arbitrary_types_allowed=True)
    clients: Dict[str, AdminClient] = {}
    kafka_config: Optional[KafkaConfig] = None
    # Per-cluster SSL config dicts (ready to merge into any confluent-kafka config)
    ssl_configs: Dict[str, Dict[str, str]] = Field(default_factory=dict)

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
            # Reset cached state so re-validation starts from a clean slate.
            self.clients.clear()
            self.ssl_configs.clear()
            self.kafka_config = kafka_config

            for cluster in kafka_config.clusters:
                try:
                    logging.info(f"Setting up Kafka client for cluster: {cluster.name}")
                    admin_config: Dict[str, Any] = {
                        "bootstrap.servers": cluster.broker,
                        "client.id": cluster.client_id,
                        "socket.timeout.ms": 15000,  # 15 second timeout
                        "api.version.request.timeout.ms": 15000,  # 15 second API version timeout
                    }

                    if cluster.security_protocol:
                        admin_config["security.protocol"] = cluster.security_protocol
                    if cluster.sasl_mechanism:
                        admin_config["sasl.mechanisms"] = cluster.sasl_mechanism
                    if cluster.username and cluster.password:
                        admin_config["sasl.username"] = cluster.username
                        admin_config["sasl.password"] = cluster.password

                    # Build auth_config — all auth keys shared with the Consumer in
                    # group_has_topic() so it can authenticate identically to the
                    # AdminClient.  Includes security.protocol, sasl.*, and ssl.*.
                    auth_config: Dict[str, Any] = {}
                    if cluster.security_protocol:
                        auth_config["security.protocol"] = cluster.security_protocol
                    if cluster.sasl_mechanism:
                        auth_config["sasl.mechanisms"] = cluster.sasl_mechanism
                    if cluster.username and cluster.password:
                        auth_config["sasl.username"] = cluster.username
                        auth_config["sasl.password"] = cluster.password

                    # SSL / mTLS configuration — mirrors kafka-mcp-server TLS logic.
                    # Temp files (base64 path) must survive past AdminClient creation
                    # because ssl_configs is also used for Consumer in group_has_topic();
                    # register atexit cleanup instead of deleting immediately.
                    ssl_config, temp_files = _build_ssl_config(cluster)
                    if ssl_config:
                        admin_config.update(ssl_config)
                        auth_config.update(ssl_config)
                        logging.info(
                            f"SSL/mTLS configured for cluster '{cluster.name}': "
                            f"protocol={admin_config.get('security.protocol')}, "
                            f"ca={ssl_config.get('ssl.ca.location', 'none')}, "
                            f"cert={ssl_config.get('ssl.certificate.location', 'none')}"
                        )
                    if auth_config:
                        self.ssl_configs[cluster.name] = auth_config
                    for tmp in temp_files:
                        atexit.register(os.unlink, tmp)

                    client = AdminClient(admin_config)
                    # Test the connection by trying to list topics with a timeout
                    # This will fail fast if the broker is not reachable
                    _ = client.list_topics(timeout=10)  # 10 second timeout
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
