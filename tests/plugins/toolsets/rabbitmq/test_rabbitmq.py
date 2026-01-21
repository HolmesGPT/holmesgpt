from holmes.plugins.toolsets.rabbitmq.toolset_rabbitmq import RabbitMQConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_rabbitmq_config():
    example = build_config_example(RabbitMQConfig)

    assert "clusters" in example
    assert isinstance(example["clusters"], list)
    assert len(example["clusters"]) == 1

    cluster0 = example["clusters"][0]
    assert cluster0["id"] == "rabbitmq"
    assert cluster0["management_url"] == "http://<your-rabbitmq-server-or-service>:15672"
    assert cluster0["username"] == "holmes_user"
    assert cluster0["password"] == "holmes_password"
    assert cluster0["request_timeout_seconds"] == 30
    assert cluster0["verify_ssl"] is True

