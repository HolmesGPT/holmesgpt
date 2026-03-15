from holmes.core.tools import ToolParameter, YAMLTool, sanitize, sanitize_params


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


class TestYAMLToolNewlineInParams:
    """End-to-end tests verifying that newlines in LLM-provided parameter values
    do not cause shell syntax errors when YAML tool commands are executed.

    Without the sanitize() fix, embedded newlines in parameters produce:
        /bin/sh: -c: line N: syntax error near unexpected token `newline'
    """

    def test_command_with_newline_in_param_does_not_produce_syntax_error(self):
        """A multi-line jq expression passed as a parameter must not break the shell."""
        tool = YAMLTool(
            name="test_echo_expr",
            description="Echo a query expression",
            command="echo {{ expression }}",
            parameters={
                "expression": ToolParameter(
                    type="string", description="A query expression"
                )
            },
        )
        multiline_expr = ".items[]\n| {name: .metadata.name,\n  ns: .metadata.namespace}"
        # Call private __invoke_command via name mangling
        output, return_code, invocation = tool._YAMLTool__invoke_command(
            params={"expression": multiline_expr},
        )
        assert "syntax error" not in output.lower()
        assert "unexpected token" not in output.lower()
        assert return_code == 0

    def test_script_with_newline_in_param_does_not_produce_syntax_error(self):
        """A multi-line parameter in a script-based tool must not break execution."""
        tool = YAMLTool(
            name="test_script_expr",
            description="Echo a query expression via script",
            script="#!/bin/bash\necho {{ expression }}",
            parameters={
                "expression": ToolParameter(
                    type="string", description="A query expression"
                )
            },
        )
        multiline_expr = "select(.status == \"running\")\n| .name"
        output, return_code, invocation = tool._YAMLTool__invoke_script(
            params={"expression": multiline_expr},
        )
        assert "syntax error" not in output.lower()
        assert "unexpected token" not in output.lower()
        assert return_code == 0
