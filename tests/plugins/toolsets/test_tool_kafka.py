import os
import random
import string
import subprocess

import pytest
from confluent_kafka.admin import NewTopic

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.kafka import (
    DescribeConsumerGroup,
    DescribeTopic,
    FindConsumerGroupsByTopic,
    KafkaToolset,
    ListKafkaConsumers,
    ListTopics,
)
from tests.conftest import create_mock_tool_invoke_context
from tests.utils.kafka import wait_for_kafka_ready

dir_path = os.path.dirname(os.path.realpath(__file__))
FIXTURE_FOLDER = os.path.join(dir_path, "fixtures", "test_tool_kafka")
KAFKA_BOOTSTRAP_SERVER = os.environ.get("KAFKA_BOOTSTRAP_SERVER")

# Use pytest.mark.skip (not skipif) to show a single grouped skip line for the entire module
# Will show: "SKIPPED [7] module.py: reason" instead of 7 separate skip lines
if not os.environ.get("KAFKA_BOOTSTRAP_SERVER"):
    pytestmark = pytest.mark.skip(reason="KAFKA_BOOTSTRAP_SERVER must be set")

CLUSTER_NAME = "kafka"

kafka_config = {
    "clusters": [
        {
            "name": CLUSTER_NAME,
            "broker": KAFKA_BOOTSTRAP_SERVER,
        }
    ]
}


@pytest.fixture(scope="module", autouse=True)
def kafka_toolset():
    """Create and configure a KafkaToolset for the local plain-text Kafka cluster."""
    kafka_toolset = KafkaToolset()
    kafka_toolset.config = kafka_config
    kafka_toolset.check_prerequisites()
    assert (
        kafka_toolset.status == ToolsetStatusEnum.ENABLED
    ), f"Prerequisites check failed for Kafka toolset: {kafka_toolset.status} / {kafka_toolset.error}"
    assert kafka_toolset.clients[CLUSTER_NAME] is not None, "Missing admin client"
    return kafka_toolset


@pytest.fixture(scope="module", autouse=True)
def admin_client(kafka_toolset):
    """Return the underlying AdminClient for direct cluster operations in tests."""
    return kafka_toolset.clients[CLUSTER_NAME]


@pytest.fixture(scope="module", autouse=True)
def docker_compose(kafka_toolset):
    """Start the docker-compose Kafka stack and verify readiness; tear it down after the session."""
    try:
        subprocess.run(
            "docker compose up -d --wait".split(),
            cwd=FIXTURE_FOLDER,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if not wait_for_kafka_ready(kafka_toolset.clients[CLUSTER_NAME]):
            raise Exception("Kafka failed to initialize properly")

        yield

    finally:
        subprocess.Popen(
            "docker compose down".split(),
            cwd=FIXTURE_FOLDER,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


@pytest.fixture(scope="module", autouse=True)
def test_topic(admin_client):
    """Create a test topic and clean it up after the test"""
    random_string = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    topic_name = f"test_topic_{random_string}"
    new_topic = NewTopic(topic_name, num_partitions=1, replication_factor=1)
    futures = admin_client.create_topics([new_topic])
    futures[topic_name].result()
    yield topic_name
    admin_client.delete_topics([topic_name])


def test_list_kafka_consumers(kafka_toolset):
    """ListKafkaConsumers should return a YAML block with a consumer_groups key."""
    tool = ListKafkaConsumers(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"kafka_cluster_name": CLUSTER_NAME}, context)
    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "consumer_groups:" in result.data
    assert (
        tool.get_parameterized_one_liner({"kafka_cluster_name": CLUSTER_NAME})
        == f"Kafka: List Consumer Groups ({CLUSTER_NAME})"
    )


def test_describe_consumer_group(kafka_toolset):
    """DescribeConsumerGroup returns SUCCESS with group metadata, or ERROR when the group is unknown."""
    tool = DescribeConsumerGroup(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {"kafka_cluster_name": CLUSTER_NAME, "group_id": "test_group"}, context
    )
    assert isinstance(result, StructuredToolResult)
    # Depending on the Kafka version/config, a non-existent group may return
    # an empty group description (SUCCESS) or a coordinator error (ERROR).
    # Both are valid tool outcomes; we only validate the payload on SUCCESS.
    if result.status == StructuredToolResultStatus.SUCCESS:
        assert result.data["group_id"] == "test_group"
    assert (
        tool.get_parameterized_one_liner({"group_id": "test_group"})
        == "Kafka: Describe Consumer Group (test_group)"
    )


def test_list_topics(kafka_toolset, test_topic):
    """ListTopics should include the newly-created test topic in its results."""
    tool = ListTopics(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"kafka_cluster_name": CLUSTER_NAME}, context)

    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "topics" in result.data
    assert test_topic in result.data.get("topics", {})

    assert (
        tool.get_parameterized_one_liner({"kafka_cluster_name": CLUSTER_NAME})
        == f"Kafka: List Kafka Topics ({CLUSTER_NAME})"
    )


def test_describe_topic(kafka_toolset, test_topic):
    """DescribeTopic (no config) returns partitions and topic metadata but no configuration block."""
    tool = DescribeTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {"kafka_cluster_name": CLUSTER_NAME, "topic_name": test_topic}, context
    )

    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "configuration" not in result.data
    metadata = result.data.get("metadata", {})
    assert "partitions" in metadata
    assert "topic" in metadata

    assert (
        tool.get_parameterized_one_liner({"topic_name": test_topic})
        == f"Kafka: Describe Topic ({test_topic})"
    )


def test_describe_topic_with_configuration(kafka_toolset, test_topic):
    """DescribeTopic with fetch_configuration=True includes a configuration block in the result."""
    tool = DescribeTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {
            "kafka_cluster_name": CLUSTER_NAME,
            "topic_name": test_topic,
            "fetch_configuration": True,
        },
        context,
    )

    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "configuration" in result.data
    metadata = result.data.get("metadata", {})
    assert "partitions" in metadata
    assert "topic" in metadata

    assert (
        tool.get_parameterized_one_liner({"topic_name": test_topic})
        == f"Kafka: Describe Topic ({test_topic})"
    )


def test_find_consumer_groups_by_topic(kafka_toolset, test_topic):
    """FindConsumerGroupsByTopic returns a clean message when no consumers are subscribed."""
    tool = FindConsumerGroupsByTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {"kafka_cluster_name": CLUSTER_NAME, "topic_name": test_topic}, context
    )

    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == f"No consumer group were found for topic {test_topic}"
    assert (
        tool.get_parameterized_one_liner({"topic_name": test_topic})
        == f"Kafka: Find Topic Consumers ({test_topic})"
    )


def test_tool_error_handling(kafka_toolset):
    """DescribeTopic on a non-existent topic returns SUCCESS with empty metadata, not an exception."""
    tool = DescribeTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {"kafka_cluster_name": CLUSTER_NAME, "topic_name": "non_existent_topic"},
        context,
    )

    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    metadata = result.data.get("metadata", {})
    assert metadata.get("topic") == "non_existent_topic"
