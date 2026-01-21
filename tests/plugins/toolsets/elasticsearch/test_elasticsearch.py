from holmes.plugins.toolsets.elasticsearch.elasticsearch import ElasticsearchConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_elasticsearch_config():
    example = build_config_example(ElasticsearchConfig)
    assert example["url"] == "https://your-cluster.es.cloud.io"
    assert example["api_key"] == "{{ env.ELASTICSEARCH_API_KEY }}"
    assert example["username"] == "your_username"
    assert example["password"] == "your_password"
    assert example["verify_ssl"] is True
    assert example["timeout"] == 10

