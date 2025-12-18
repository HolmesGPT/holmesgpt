import re
from typing import Optional

from holmes.plugins.toolsets.bash.common.config import (
    BashExecutorConfig,
    KubectlImageConfig,
)


def validate_image_and_commands(
    image: str, container_command: str, config: Optional[BashExecutorConfig]
) -> None:
    """
    Validate that the image is in the whitelist and commands are allowed.
    Raises ValueError if validation fails.
    """
    if not config or not config.kubectl or not config.kubectl.allowed_images:
        raise ValueError(
            "The command `kubectl run` is not allowed. The user must whitelist specific images and commands but none have been configured."
        )

    # Find matching image config
    image_config: Optional[KubectlImageConfig] = None
    for img_config in config.kubectl.allowed_images:
        if img_config.image == image:
            image_config = img_config
            break

    if not image_config:
        allowed_images = [img.image for img in config.kubectl.allowed_images]
        raise ValueError(
            f"Image '{image}' not allowed. Allowed images: {', '.join(allowed_images)}"
        )

    # Validate commands against allowed patterns
    command_allowed = False
    for allowed_pattern in image_config.allowed_commands:
        if re.match(allowed_pattern, container_command):
            command_allowed = True
            break

    if not command_allowed:
        raise ValueError(
            f"Command '{container_command}' not allowed for image '{image}'. "
            f"Allowed patterns: {', '.join(image_config.allowed_commands)}"
        )
