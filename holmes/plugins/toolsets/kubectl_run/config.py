"""Configuration for the kubectl-run toolset."""

from pydantic import Field

from holmes.utils.pydantic_utils import ToolsetConfig


class KubectlImageConfig(ToolsetConfig):
    """Configuration for an allowed image in kubectl run."""

    image: str = Field(
        title="Image",
        description="Container image name",
    )
    allowed_commands: list[str] = Field(
        title="Allowed Commands",
        description="List of allowed commands for this image",
    )


class KubectlRunConfig(ToolsetConfig):
    """Configuration for the kubectl-run toolset."""

    allowed_images: list[KubectlImageConfig] = Field(
        default_factory=list,
        title="Allowed Images",
        description="List of allowed images and their permitted commands",
    )
