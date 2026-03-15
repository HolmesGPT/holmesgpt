import shlex
import subprocess

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


class TestNewlineRenderedCommandIntegrity:
    """Verify that the sanitize fix prevents newline corruption in rendered
    commands by comparing old (broken) vs new (fixed) behavior end-to-end.

    These tests prove:
    1. WITHOUT the fix: rendered commands contain literal newlines, splitting
       them into multiple shell lines and corrupting argument parsing.
    2. WITH the fix: rendered commands are single-line with clean arguments.
    """

    def _sanitize_without_fix(self, param):
        """Simulate old sanitize() that preserves newlines."""
        if param == "":
            return ""
        return shlex.quote(str(param))

    def test_old_sanitize_produces_multiline_command(self):
        """Without the fix, a newline in a param creates a multi-line command."""
        from jinja2 import Template

        template = Template("kubectl logs {{ pod_name }} -n {{ namespace }}")
        pod_name = "my-pod\n-n kube-system"

        # Old behavior: newline preserved
        old_quoted = self._sanitize_without_fix(pod_name)
        old_cmd = template.render(pod_name=old_quoted, namespace="default")
        assert "\n" in old_cmd, "Old sanitize should preserve newlines (the bug)"
        assert old_cmd.count("\n") >= 1

    def test_new_sanitize_produces_singleline_command(self):
        """With the fix, newlines are replaced so the command stays single-line."""
        from jinja2 import Template

        template = Template("kubectl logs {{ pod_name }} -n {{ namespace }}")
        pod_name = "my-pod\n-n kube-system"

        new_quoted = sanitize(pod_name)
        new_cmd = template.render(pod_name=new_quoted, namespace="default")
        assert "\n" not in new_cmd, "Fixed sanitize must strip newlines"

    def test_old_sanitize_corrupts_argument_in_subprocess(self):
        """Without the fix, subprocess receives an argument with an embedded
        newline, which corrupts the pod name passed to the tool."""
        from jinja2 import Template

        # Use echo + wc -l to count lines — a newline in the argument
        # means echo outputs 2 lines instead of 1.
        template = Template("echo {{ pod_name }} | wc -l")
        pod_name = "my-pod\n-n kube-system"

        old_quoted = self._sanitize_without_fix(pod_name)
        old_cmd = template.render(pod_name=old_quoted)

        result = subprocess.run(
            old_cmd,
            shell=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # The embedded newline causes echo to output 2 lines
        line_count = int(result.stdout.strip())
        assert line_count == 2, f"Expected 2 lines (newline in arg), got {line_count}"

    def test_new_sanitize_clean_argument_in_subprocess(self):
        """With the fix, subprocess receives a clean single-line argument."""
        from jinja2 import Template

        template = Template("echo {{ pod_name }} | wc -l")
        pod_name = "my-pod\n-n kube-system"

        new_quoted = sanitize(pod_name)
        new_cmd = template.render(pod_name=new_quoted)

        result = subprocess.run(
            new_cmd,
            shell=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert result.returncode == 0
        # With the fix, the argument is single-line
        line_count = int(result.stdout.strip())
        assert line_count == 1, f"Expected 1 line (no newline), got {line_count}"


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
