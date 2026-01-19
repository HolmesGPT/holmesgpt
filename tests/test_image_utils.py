"""Tests for image utilities."""
import base64
from pathlib import Path

import pytest

from holmes.utils.image_utils import (
    SUPPORTED_IMAGE_FORMATS,
    get_image_mime_type,
    image_to_base64_data_uri,
    process_image_paths,
    validate_image_file,
)


def test_get_image_mime_type():
    """Test MIME type detection."""
    assert get_image_mime_type(Path("test.jpg")) == "image/jpeg"
    assert get_image_mime_type(Path("test.jpeg")) == "image/jpeg"
    assert get_image_mime_type(Path("test.png")) == "image/png"
    assert get_image_mime_type(Path("test.gif")) == "image/gif"
    assert get_image_mime_type(Path("test.webp")) == "image/webp"
    assert get_image_mime_type(Path("test.txt")) is None
    assert get_image_mime_type(Path("test.JPG")) == "image/jpeg"  # Case insensitive


def test_validate_image_file_missing(tmp_path):
    """Test validation fails for missing file."""
    missing_file = tmp_path / "missing.jpg"
    with pytest.raises(ValueError, match="not found"):
        validate_image_file(missing_file)


def test_validate_image_file_unsupported(tmp_path):
    """Test validation fails for unsupported format."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("not an image")

    with pytest.raises(ValueError, match="Unsupported image format"):
        validate_image_file(test_file)


def test_image_to_base64_data_uri(tmp_path):
    """Test converting image to base64 data URI."""
    # Create a tiny valid PNG (1x1 transparent pixel)
    png_data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    test_file = tmp_path / "test.png"
    test_file.write_bytes(png_data)

    result = image_to_base64_data_uri(test_file)

    assert result.startswith("data:image/png;base64,")
    # Verify it contains the expected base64 data
    base64_part = result.split(",")[1]
    assert len(base64_part) > 50  # Has actual base64 data
    # Verify we can decode it back
    decoded = base64.b64decode(base64_part)
    assert decoded == png_data


def test_process_image_paths(tmp_path):
    """Test processing multiple images."""
    # Create two test images
    png_data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    img1 = tmp_path / "test1.png"
    img1.write_bytes(png_data)

    img2 = tmp_path / "test2.png"
    img2.write_bytes(png_data)

    results = process_image_paths([img1, img2])

    assert len(results) == 2
    assert all(r.startswith("data:image/png;base64,") for r in results)


def test_process_image_paths_empty():
    """Test processing empty list."""
    assert process_image_paths([]) == []
    assert process_image_paths(None) == []


def test_process_image_paths_with_invalid(tmp_path):
    """Test processing with invalid image raises error."""
    valid_png = tmp_path / "valid.png"
    png_data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    valid_png.write_bytes(png_data)

    invalid_img = tmp_path / "invalid.jpg"
    # File doesn't exist

    with pytest.raises(ValueError, match="not found"):
        process_image_paths([valid_png, invalid_img])
