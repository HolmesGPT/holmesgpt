from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
    ServiceNowTablesConfig,
)
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_servicenow_tables_config():
    example = build_config_example(ServiceNowTablesConfig)

    assert example["api_key"] == "now_1234567890abcdef"
    assert example["instance_url"] == "https://your-instance.service-now.com"
    assert example["api_key_header"] == "x-sn-apikey"

