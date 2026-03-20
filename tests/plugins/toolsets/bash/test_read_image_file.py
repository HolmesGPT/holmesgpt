import base64
from pathlib import Path
from unittest.mock import Mock

import pytest

from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext
from holmes.plugins.toolsets.bash.bash_toolset import ReadImageFile


@pytest.fixture
def tool():
    """Create a ReadImageFile tool with a mock toolset."""
    mock_toolset = Mock()
    mock_toolset.name = "bash"
    return ReadImageFile(mock_toolset)


@pytest.fixture
def mock_context():
    return Mock(spec=ToolInvokeContext)


class TestReadImageFile:
    def test_read_png(self, tool, mock_context, tmp_path):
        """Read a valid PNG file and verify base64 output."""
        img_data = b"\x89PNG\r\n\x1a\nfake-png-data"
        img_file = tmp_path / "test.png"
        img_file.write_bytes(img_data)

        result = tool._invoke({"file_path": str(img_file)}, mock_context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.images is not None
        assert len(result.images) == 1
        assert result.images[0]["mimeType"] == "image/png"
        assert base64.b64decode(result.images[0]["data"]) == img_data

    def test_read_jpeg(self, tool, mock_context, tmp_path):
        """Read a JPEG file."""
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        result = tool._invoke({"file_path": str(img_file)}, mock_context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.images[0]["mimeType"] == "image/jpeg"

    def test_missing_file_path(self, tool, mock_context):
        """Missing file_path parameter returns error."""
        result = tool._invoke({"file_path": ""}, mock_context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "required" in result.error

    def test_relative_path_rejected(self, tool, mock_context):
        """Relative paths are rejected."""
        result = tool._invoke({"file_path": "relative/path.png"}, mock_context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "absolute" in result.error

    def test_nonexistent_file(self, tool, mock_context):
        """Non-existent file returns error."""
        result = tool._invoke({"file_path": "/tmp/nonexistent_12345.png"}, mock_context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not found" in result.error.lower()

    def test_unsupported_extension(self, tool, mock_context, tmp_path):
        """Unsupported image format returns error."""
        bmp_file = tmp_path / "image.bmp"
        bmp_file.write_bytes(b"BM fake bmp")

        result = tool._invoke({"file_path": str(bmp_file)}, mock_context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Unsupported" in result.error

    def test_file_too_large(self, tool, mock_context, tmp_path):
        """Files exceeding 20MB are rejected."""
        big_file = tmp_path / "huge.png"
        # Create a file just over the 20MB limit
        big_file.write_bytes(b"\x00" * (20 * 1024 * 1024 + 1))

        result = tool._invoke({"file_path": str(big_file)}, mock_context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "too large" in result.error.lower()

    def test_webp_and_gif(self, tool, mock_context, tmp_path):
        """WebP and GIF formats are supported."""
        for ext, mime in [(".webp", "image/webp"), (".gif", "image/gif")]:
            img_file = tmp_path / f"test{ext}"
            img_file.write_bytes(b"fake-data")
            result = tool._invoke({"file_path": str(img_file)}, mock_context)
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.images[0]["mimeType"] == mime
