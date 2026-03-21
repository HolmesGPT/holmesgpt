import base64

import pytest

from holmes.core.tools_utils.filesystem_result_storage import save_images, save_large_result


class TestSaveImages:
    def test_save_single_png(self, tmp_path):
        """Save a single PNG image and verify the file contents."""
        pixel = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
        images = [{"data": pixel, "mimeType": "image/png"}]
        paths = save_images(tmp_path, "tool1", "call1", images)

        assert len(paths) == 1
        assert paths[0].endswith(".png")
        assert tmp_path / "tool1_call1_img0.png" == __import__("pathlib").Path(paths[0])
        assert __import__("pathlib").Path(paths[0]).read_bytes() == b"\x89PNG\r\n\x1a\n"

    def test_save_multiple_images_different_types(self, tmp_path):
        """Save multiple images with different MIME types."""
        images = [
            {"data": base64.b64encode(b"png-data").decode(), "mimeType": "image/png"},
            {"data": base64.b64encode(b"jpg-data").decode(), "mimeType": "image/jpeg"},
            {"data": base64.b64encode(b"gif-data").decode(), "mimeType": "image/gif"},
            {"data": base64.b64encode(b"webp-data").decode(), "mimeType": "image/webp"},
        ]
        paths = save_images(tmp_path, "tool", "call", images)

        assert len(paths) == 4
        assert paths[0].endswith(".png")
        assert paths[1].endswith(".jpg")
        assert paths[2].endswith(".gif")
        assert paths[3].endswith(".webp")

    def test_save_empty_list(self, tmp_path):
        """Empty image list returns empty paths."""
        paths = save_images(tmp_path, "tool", "call", [])
        assert paths == []

    def test_save_with_special_chars_in_names(self, tmp_path):
        """Special characters in tool/call names are sanitized."""
        images = [{"data": base64.b64encode(b"data").decode(), "mimeType": "image/png"}]
        paths = save_images(tmp_path, "my/tool:v2", "call-id.123", images)

        assert len(paths) == 1
        # Special chars replaced with underscores
        filename = __import__("pathlib").Path(paths[0]).name
        assert "/" not in filename
        assert ":" not in filename
        assert "." not in filename.rsplit(".", 1)[0]  # dots sanitized except extension

    def test_save_skips_unsupported_mime_type(self, tmp_path):
        """Unsupported MIME types are skipped instead of silently mislabeled."""
        images = [{"data": base64.b64encode(b"data").decode(), "mimeType": "image/bmp"}]
        paths = save_images(tmp_path, "tool", "call", images)

        assert len(paths) == 0

    def test_save_skips_invalid_base64(self, tmp_path):
        """Invalid base64 data is skipped with a warning, other images still saved."""
        images = [
            {"data": "!!!not-base64!!!", "mimeType": "image/png"},
            {"data": base64.b64encode(b"good").decode(), "mimeType": "image/png"},
        ]
        paths = save_images(tmp_path, "tool", "call", images)

        # First image fails, second succeeds
        assert len(paths) == 1
        assert "img1" in paths[0]


class TestSaveLargeResult:
    def test_save_text_result(self, tmp_path):
        """Save a text result and verify contents."""
        path = save_large_result(tmp_path, "test_tool", "call_1", "hello world")
        assert path is not None
        assert path.endswith(".txt")
        assert __import__("pathlib").Path(path).read_text() == "hello world"

    def test_save_json_result(self, tmp_path):
        """Save a JSON result with correct extension."""
        path = save_large_result(
            tmp_path, "test_tool", "call_1", '{"key": "value"}', is_json=True
        )
        assert path is not None
        assert path.endswith(".json")
