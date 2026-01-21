from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.utils.pydantic_utils import build_config_example


def test_build_config_example_bash_executor_config():
    example = build_config_example(BashExecutorConfig)

    assert "kubectl" in example
    assert "allowed_images" in example["kubectl"]
    assert isinstance(example["kubectl"]["allowed_images"], list)
    assert len(example["kubectl"]["allowed_images"]) == 1

    image0 = example["kubectl"]["allowed_images"][0]
    assert image0["image"] == "busybox:1.36"
    assert image0["allowed_commands"] == ["ping", "curl", "wget"]

