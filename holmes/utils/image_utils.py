"""Utilities for processing images for vision models."""
import base64
from pathlib import Path
from typing import List, Optional

# Supported image formats
SUPPORTED_IMAGE_FORMATS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def get_image_mime_type(file_path: Path) -> Optional[str]:
    """Get MIME type for an image file based on extension.

    Args:
        file_path: Path to image file

    Returns:
        MIME type string or None if unsupported
    """
    suffix = file_path.suffix.lower()
    return SUPPORTED_IMAGE_FORMATS.get(suffix)


def validate_image_file(file_path: Path) -> None:
    """Validate that a file is a supported image format.

    Args:
        file_path: Path to image file

    Raises:
        ValueError: If file doesn't exist or has unsupported format
    """
    if not file_path.exists():
        raise ValueError(f"Image file not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    mime_type = get_image_mime_type(file_path)
    if mime_type is None:
        supported = ", ".join(SUPPORTED_IMAGE_FORMATS.keys())
        raise ValueError(
            f"Unsupported image format: {file_path.suffix}. "
            f"Supported formats: {supported}"
        )


def image_to_base64_data_uri(file_path: Path) -> str:
    """Convert an image file to a base64 data URI.

    Args:
        file_path: Path to image file

    Returns:
        Base64 data URI string (e.g., "data:image/jpeg;base64,/9j/...")

    Raises:
        ValueError: If file is invalid or unsupported format
    """
    validate_image_file(file_path)

    mime_type = get_image_mime_type(file_path)
    with open(file_path, "rb") as f:
        image_data = f.read()

    base64_data = base64.b64encode(image_data).decode("utf-8")
    return f"data:{mime_type};base64,{base64_data}"


def process_image_paths(image_paths: Optional[List[Path]], console=None) -> List[str]:
    """Process a list of image paths into base64 data URIs.

    Args:
        image_paths: List of paths to image files
        console: Optional Rich console for progress output

    Returns:
        List of base64 data URI strings

    Raises:
        ValueError: If any image is invalid
    """
    if not image_paths:
        return []

    data_uris = []
    for image_path in image_paths:
        try:
            if console:
                console.print(f"[dim]Loading image: {image_path}[/dim]")
            data_uri = image_to_base64_data_uri(image_path)
            data_uris.append(data_uri)
        except ValueError as e:
            if console:
                console.print(f"[bold red]Error loading image:[/bold red] {e}")
            raise

    return data_uris
