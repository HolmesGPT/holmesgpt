from typing import Optional

from pydantic import BaseModel, Field

from holmes.utils.pydantic_utils import build_config_example


class NestedModel(BaseModel):
    # default is None -> should use examples[0]
    region: Optional[str] = Field(default=None, examples=["us-east-1"])
    # default is used
    enabled: bool = True


class ParentModel(BaseModel):
    # default is used
    count: int = 3
    # default_factory is used
    headers: dict[str, str] = Field(default_factory=dict)
    # default is None -> should use examples[0]
    tenant_id: Optional[str] = Field(default=None, examples=["{{ env.TENANT_ID }}"])
    # no default/default_factory/examples -> placeholder
    api_key: str
    # Optional[BaseModel] with default None and no examples -> recurse
    nested: Optional[NestedModel] = None


def test_build_config_example_uses_default_default_factory_examples_nested_and_placeholder():
    example = build_config_example(ParentModel)

    assert example["count"] == 3
    assert example["headers"] == {}
    assert example["tenant_id"] == "{{ env.TENANT_ID }}"
    assert example["api_key"] == "your_api_key"

    # Nested recursion
    assert example["nested"] == {"region": "us-east-1", "enabled": True}

