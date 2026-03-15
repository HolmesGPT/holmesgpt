from holmes.core.tools import sanitize, sanitize_params


class TestSanitize:
    def test_empty_string_returns_empty(self):
        assert sanitize("") == ""

    def test_simple_string_is_quoted(self):
        result = sanitize("hello")
        assert result == "hello"

    def test_string_with_spaces_is_quoted(self):
        result = sanitize("hello world")
        assert "hello world" in result

    def test_newline_replaced_with_space(self):
        result = sanitize("hello\nworld")
        assert "\n" not in result
        assert "hello world" in result

    def test_carriage_return_replaced_with_space(self):
        result = sanitize("hello\rworld")
        assert "\r" not in result
        assert "hello world" in result

    def test_crlf_replaced_with_spaces(self):
        result = sanitize("hello\r\nworld")
        assert "\r" not in result
        assert "\n" not in result

    def test_multiple_newlines_replaced(self):
        result = sanitize("a\nb\nc")
        assert "\n" not in result
        assert "a b c" in result


class TestSanitizeParams:
    def test_newlines_stripped_from_all_params(self):
        params = {"name": "my\nresource", "namespace": "default\n"}
        result = sanitize_params(params)
        for v in result.values():
            assert "\n" not in v
