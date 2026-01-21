from holmes.plugins.toolsets.newrelic.newrelic import NewrelicConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_newrelic_config():
    example = build_config_example(NewrelicConfig)

    assert example["nr_api_key"] == "NRAK-XXXXXXXXXXXXXXXXXXXXXXXXXX"
    assert example["nr_account_id"] == "1234567"
    assert example["is_eu_datacenter"] is False
    assert example["enable_multi_account"] is False

