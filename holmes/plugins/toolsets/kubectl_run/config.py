"""Configuration for the kubectl-run toolset."""

from pydantic import BaseModel


class KubectlImageConfig(BaseModel):
    """Configuration for an allowed image in kubectl run."""

    image: str
    allowed_commands: list[str]


class KubectlRunConfig(BaseModel):
    """Configuration for the kubectl-run toolset."""

    allowed_images: list[KubectlImageConfig] = []
