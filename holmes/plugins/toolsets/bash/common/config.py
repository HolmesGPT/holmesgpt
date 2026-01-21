from pydantic import BaseModel, Field

from holmes.utils.pydantic_utils import build_config_example


class KubectlImageConfig(BaseModel):
    image: str = Field(
        description="Container image to allow",
        examples=["busybox:1.36"],
    )
    allowed_commands: list[str] = Field(
        description="List of allowed commands for this image",
        examples=[["ping", "curl", "wget"]],
    )


class KubectlConfig(BaseModel):
    allowed_images: list[KubectlImageConfig] = Field(
        default=[],
        description="List of allowed container images for kubectl run",
        examples=[[build_config_example(KubectlImageConfig)]]
    )


class BashExecutorConfig(BaseModel):
    kubectl: KubectlConfig = Field(
        default_factory=KubectlConfig,
        description="Configuration for kubectl commands",
    )
