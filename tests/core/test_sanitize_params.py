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

    def test_special_characters_are_quoted(self):
        result = sanitize("hello; rm -rf /")
        # shlex.quote wraps in single quotes to prevent injection
        assert result.startswith("'")

    def test_newlines_preserved_in_shlex_quote(self):
        """sanitize() delegates to shlex.quote which preserves newlines inside quotes."""
        result = sanitize("hello\nworld")
        assert "\n" in result


class TestSanitizeParams:
    def test_all_values_are_sanitized(self):
        params = {"name": "my resource", "namespace": "default"}
        result = sanitize_params(params)
        assert "'my resource'" == result["name"]
        assert result["namespace"] == "default"


class TestKubernetesJqToolNewlineHandling:
    """Tests that the kubernetes_jq_query tool script handles multi-line jq expressions.

    LLMs sometimes write multi-line jq expressions. The kubernetes_jq_query script
    collapses them to a single line via tr before passing to jq.
    """

    def test_jq_script_collapses_multiline_expression(self):
        """Simulate the JQ_FILTER assignment + tr pattern from kubernetes.yaml."""
        multiline_jq = ".items[]\n| select(.status == \"unhealthy\")\n| .name"

        # This mirrors what the kubernetes.yaml script does:
        # JQ_FILTER=<shlex-quoted expr>
        # JQ_FILTER=$(printf '%s' "$JQ_FILTER" | tr '\n\r' '  ')
        script = f"""#!/bin/bash
JQ_FILTER={sanitize(multiline_jq)}
JQ_FILTER=$(printf '%s' "$JQ_FILTER" | tr '\\n\\r' '  ')
echo '{{"items":[{{"status":"unhealthy","name":"pod-a"}},{{"status":"healthy","name":"pod-b"}}]}}' | jq -c "$JQ_FILTER"
"""
        result = subprocess.run(
            script,
            shell=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert result.returncode == 0
        assert "pod-a" in result.stdout
        assert "pod-b" not in result.stdout

    def test_singleline_jq_still_works(self):
        """A normal single-line jq expression still works after the tr step."""
        jq_expr = '.items[] | select(.status == "unhealthy") | .name'

        script = f"""#!/bin/bash
JQ_FILTER={sanitize(jq_expr)}
JQ_FILTER=$(printf '%s' "$JQ_FILTER" | tr '\\n\\r' '  ')
echo '{{"items":[{{"status":"unhealthy","name":"pod-a"}},{{"status":"healthy","name":"pod-b"}}]}}' | jq -c "$JQ_FILTER"
"""
        result = subprocess.run(
            script,
            shell=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert result.returncode == 0
        assert "pod-a" in result.stdout


class TestYAMLToolNewlineInParams:
    """Newlines in shlex-quoted params inside single quotes are valid bash.
    These tests verify that commands and scripts don't break with newlines in params.
    """

    def test_command_with_newline_in_param_does_not_produce_syntax_error(self):
        """A multi-line expression passed as a parameter must not break the shell."""
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
