from holmes.plugins.toolsets.atlas_mongodb.mongodb_atlas import MongoDBConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_mongodb_atlas_config():
    example = build_config_example(MongoDBConfig)

    assert example["public_key"] == "your_public_key"
    assert example["private_key"] == "your_private_key"
    assert example["project_id"] == "your_project_id"

