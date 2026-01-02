import os
import random
import string
import subprocess

import pytest
from confluent_kafka.admin import NewTopic

from holmes.core.tools import ToolsetStatusEnum
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

kafka_config = {
    "kafka_clusters": [
        {
            "name": "kafka",
            "kafka_broker": KAFKA_BOOTSTRAP_SERVER,
        }
    ]
}


@pytest.fixture(scope="module", autouse=True)
def kafka_toolset():
    kafka_toolset = KafkaToolset()
    kafka_toolset.config = kafka_config
    kafka_toolset.check_prerequisites()
    assert (
        kafka_toolset.status == ToolsetStatusEnum.ENABLED
    ), f"Prerequisites check failed for Kafka toolset: {kafka_toolset.status} / {kafka_toolset.error}"
    assert kafka_toolset.clients["kafka"] is not None, "Missing admin client"
    return kafka_toolset


@pytest.fixture(scope="module", autouse=True)
def admin_client(kafka_toolset):
    return kafka_toolset.clients["kafka"]


@pytest.fixture(scope="module", autouse=True)
def docker_compose(kafka_toolset):
    try:
        subprocess.run(
            "docker compose up -d --wait".split(),
            cwd=FIXTURE_FOLDER,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if not wait_for_kafka_ready(kafka_toolset.clients["kafka"]):
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
    tool = ListKafkaConsumers(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({}, context)
    assert "consumer_groups:" in result
    assert (
        tool.get_parameterized_one_liner({})
        == "Listed all Kafka consumer groups in the cluster"
    )


def test_describe_consumer_group(kafka_toolset):
    tool = DescribeConsumerGroup(kafka_toolset)

    context = create_mock_tool_invoke_context()
    result = tool.invoke({"group_id": "test_group"}, context)
    assert "group_id: test_group" in result
    assert (
        tool.get_parameterized_one_liner({"group_id": "test_group"})
        == "Described consumer group: test_group"
    )


def test_list_topics(kafka_toolset, test_topic):
    tool = ListTopics(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({}, context)

    assert "topics" in result
    assert test_topic in result

    assert (
        tool.get_parameterized_one_liner({}) == "Listed all Kafka topics in the cluster"
    )


def test_describe_topic(kafka_toolset, test_topic):
    tool = DescribeTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"topic_name": test_topic}, context)

    assert "configuration:" not in result
    assert "partitions:" in result
    assert "topic:" in result

    assert (
        tool.get_parameterized_one_liner({"topic_name": test_topic})
        == f"Described topic: {test_topic}"
    )


def test_describe_topic_with_configuration(kafka_toolset, test_topic):
    tool = DescribeTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {"topic_name": test_topic, "fetch_configuration": True}, context
    )

    assert "configuration:" in result
    assert "partitions:" in result
    assert "topic:" in result

    assert (
        tool.get_parameterized_one_liner({"topic_name": test_topic})
        == f"Described topic: {test_topic}"
    )


def test_find_consumer_groups_by_topic(kafka_toolset, test_topic):
    tool = FindConsumerGroupsByTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"topic_name": test_topic}, context)

    assert result == f"No consumer group were found for topic {test_topic}"
    assert (
        tool.get_parameterized_one_liner({"topic_name": test_topic})
        == f"Found consumer groups for topic: {test_topic}"
    )


def test_tool_error_handling(kafka_toolset):
    tool = DescribeTopic(kafka_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"topic_name": "non_existent_topic"}, context)

    assert isinstance(result, str)
    assert "topic: non_existent_topic" in result
