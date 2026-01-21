from holmes.plugins.toolsets.kafka import KafkaConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_kafka_config():
    example = build_config_example(KafkaConfig)

    assert "kafka_clusters" in example
    assert isinstance(example["kafka_clusters"], list)
    assert len(example["kafka_clusters"]) == 1

    cluster0 = example["kafka_clusters"][0]
    assert cluster0["name"] == "us-west-kafka"
    assert cluster0["kafka_broker"] == "broker1.example.com:9092,broker2.example.com:9092"
    assert cluster0["kafka_security_protocol"] == "SASL_SSL"
    assert cluster0["kafka_sasl_mechanism"] == "PLAIN"
    assert cluster0["kafka_username"] == "{{ env.KAFKA_USERNAME }}"
    assert cluster0["kafka_password"] == "{{ env.KAFKA_PASSWORD }}"
    assert cluster0["kafka_client_id"] == "holmes-kafka-client"

