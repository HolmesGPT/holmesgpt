"""
Unit tests for kubernetes_jq_query and kubernetes_count script rendering.

Verifies that jq expressions are written to temp files and invoked via `jq -f`
instead of being embedded inline, preventing shell interpretation of pipe
characters and other special characters in complex jq expressions.
"""

import os
import shlex
import subprocess
import tempfile

from jinja2 import Template

from holmes.core.tools import sanitize_params
from holmes.plugins.toolsets import load_toolsets_from_file

# Path to the kubernetes.yaml toolset
KUBERNETES_YAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "..",
    "holmes",
    "plugins",
    "toolsets",
    "kubernetes.yaml",
)

# A complex jq expression similar to what LLMs generate, with pipes, regex,
# and semicolons that would break if interpreted by the shell
COMPLEX_JQ_EXPR = (
    '.items[] | select(.metadata.namespace | test("^openshift-|^kube-|^default$") | not)'
    " | .spec.containers[] | .env // [] | .[]"
    " | select(.value != null and (.valueFrom == null))"
    ' | select(.name | test("PASSWORD|SECRET|API_KEY|CREDENTIAL"; "i"))'
    ' | select(.name | test("SERVICE_PORT|SERVICE_HOST|KUBERNETES_|_PORT_|LABEL_KEY|INSTANCE_LABEL"; "i") | not)'
    " | .name"
)


def _get_tool_script(tool_name: str) -> str:
    """Load and return the raw script template for a named tool."""
    toolsets = load_toolsets_from_file(KUBERNETES_YAML_PATH)
    kubernetes_core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
    tool = next(t for t in kubernetes_core.tools if t.name == tool_name)
    assert tool.script is not None, f"{tool_name} should have a script"
    return tool.script


def _render_script(script_template: str, kind: str, jq_expr: str) -> str:
    """Render a script template the same way tools.py does."""
    params = sanitize_params({"kind": kind, "jq_expr": jq_expr})
    context = {**params, "env": os.environ}
    expanded = os.path.expandvars(script_template)
    template = Template(expanded)
    return template.render(context)


class TestJqExpressionWrittenToFile:
    """Test that jq expressions are written to a temp file, not passed inline."""

    def test_jq_query_script_uses_jq_f_flag(self):
        """kubernetes_jq_query must use jq -f (file) not inline expression."""
        script = _get_tool_script("kubernetes_jq_query")
        rendered = _render_script(script, "deployments", COMPLEX_JQ_EXPR)

        # Must write expression to a temp file
        assert "jq_file=$(mktemp)" in rendered
        assert "printf '%s'" in rendered
        assert '> "$jq_file"' in rendered

        # Must use jq -f to read from file
        assert 'jq -c -f "$jq_file"' in rendered

        # Must NOT have inline jq -c with the expression directly
        # (the old vulnerable pattern)
        for line in rendered.split("\n"):
            line = line.strip()
            if line.startswith("MATCHES=$(") and "jq -c" in line:
                assert "-f" in line, (
                    f"jq -c call in MATCHES assignment must use -f flag: {line}"
                )

    def test_kubernetes_count_script_uses_jq_f_flag(self):
        """kubernetes_count must use jq -f (file) not inline expression."""
        script = _get_tool_script("kubernetes_count")
        rendered = _render_script(script, "pods", COMPLEX_JQ_EXPR)

        assert "jq_file=$(mktemp)" in rendered
        assert "printf '%s'" in rendered
        assert '> "$jq_file"' in rendered
        assert 'jq -c -r -f "$jq_file"' in rendered

    def test_jq_file_cleaned_up_on_success(self):
        """Temp jq file must be cleaned up in the final cleanup path."""
        script = _get_tool_script("kubernetes_jq_query")
        rendered = _render_script(script, "deployments", ".items[]")

        # The final cleanup (outside the loop) must remove both files
        # Find lines with rm -f that are NOT inside error-exit blocks
        lines = rendered.split("\n")
        cleanup_lines = [
            line.strip()
            for line in lines
            if "rm -f" in line and "$jq_file" in line
        ]
        assert len(cleanup_lines) > 0, "Must clean up $jq_file in at least one rm -f"

    def test_jq_file_cleaned_up_on_error_exits(self):
        """Temp jq file must be cleaned up in all error-exit paths."""
        script = _get_tool_script("kubernetes_jq_query")
        rendered = _render_script(script, "deployments", ".items[]")

        lines = rendered.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("rm -f") and "$err_file" in stripped:
                assert "$jq_file" in stripped, (
                    f"Line {i + 1}: rm -f removes $err_file but not $jq_file: {stripped}"
                )


class TestJqExpressionShellSafety:
    """Test that the jq expression is never directly interpreted by the shell."""

    def test_printf_writes_correct_expression_to_file(self):
        """printf with shlex-quoted expr must produce the raw expression in a file."""
        sanitized = shlex.quote(COMPLEX_JQ_EXPR)

        # Simulate what the script does: printf '%s' <quoted_expr> > file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False
        ) as f:
            f.write(f"#!/bin/bash\njq_file=$(mktemp)\n")
            f.write(f"printf '%s' {sanitized} > \"$jq_file\"\n")
            f.write("cat \"$jq_file\"\n")
            f.write("rm -f \"$jq_file\"\n")
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            result = subprocess.run(
                script_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            # The file must contain the EXACT original expression
            assert result.stdout.strip() == COMPLEX_JQ_EXPR
        finally:
            os.unlink(script_path)

    def test_jq_f_parses_expression_from_file(self):
        """jq -f must correctly read and apply the expression from a temp file."""
        sanitized = shlex.quote(COMPLEX_JQ_EXPR)

        test_json = (
            '{"items":[{"metadata":{"name":"test","namespace":"myns"},'
            '"spec":{"containers":[{"env":[{"name":"DB_PASSWORD","value":"s3cret"}]}]}}]}'
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False
        ) as f:
            f.write("#!/bin/bash\n")
            f.write("jq_file=$(mktemp)\n")
            f.write(f"printf '%s' {sanitized} > \"$jq_file\"\n")
            f.write(f"echo '{test_json}' | jq -c -f \"$jq_file\"\n")
            f.write("rm -f \"$jq_file\"\n")
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            result = subprocess.run(
                script_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            # The jq expression should find the PASSWORD env var name
            assert result.stdout.strip() == '"DB_PASSWORD"'
        finally:
            os.unlink(script_path)

    def test_no_shell_interpretation_of_pipes_in_jq_expr(self):
        """Shell must NOT interpret | in jq expressions as pipe operators."""
        sanitized = shlex.quote(COMPLEX_JQ_EXPR)

        # This script would produce "command not found" errors if the expression
        # were interpreted by the shell instead of being written to a file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False
        ) as f:
            f.write("#!/bin/bash\n")
            f.write("jq_file=$(mktemp)\n")
            f.write(f"printf '%s' {sanitized} > \"$jq_file\"\n")
            f.write("cat \"$jq_file\"\n")
            f.write("rm -f \"$jq_file\"\n")
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            result = subprocess.run(
                script_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # No "command not found" errors in output
            assert "command not found" not in result.stdout
            assert "command not found" not in result.stderr
            assert result.returncode == 0
        finally:
            os.unlink(script_path)

    def test_expression_with_semicolons_and_regex_flags(self):
        """jq expressions with semicolons (e.g. test("x"; "i")) must work."""
        expr_with_semicolons = '.items[] | select(.name | test("foo"; "i")) | .name'
        sanitized = shlex.quote(expr_with_semicolons)

        test_json = '{"items":[{"name":"FooBar"},{"name":"baz"}]}'

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False
        ) as f:
            f.write("#!/bin/bash\n")
            f.write("jq_file=$(mktemp)\n")
            f.write(f"printf '%s' {sanitized} > \"$jq_file\"\n")
            f.write(f"echo '{test_json}' | jq -c -f \"$jq_file\"\n")
            f.write("rm -f \"$jq_file\"\n")
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            result = subprocess.run(
                script_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert result.stdout.strip() == '"FooBar"'
        finally:
            os.unlink(script_path)

    def test_expression_with_dollar_signs(self):
        """jq expressions with $ (e.g. regex anchors) must not be expanded."""
        expr_with_dollar = '.items[] | select(.name | test("^default$")) | .name'
        sanitized = shlex.quote(expr_with_dollar)

        test_json = '{"items":[{"name":"default"},{"name":"default-ns"},{"name":"other"}]}'

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False
        ) as f:
            f.write("#!/bin/bash\n")
            f.write("jq_file=$(mktemp)\n")
            f.write(f"printf '%s' {sanitized} > \"$jq_file\"\n")
            f.write(f"echo '{test_json}' | jq -c -f \"$jq_file\"\n")
            f.write("rm -f \"$jq_file\"\n")
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            result = subprocess.run(
                script_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            # Only exact "default" should match, not "default-ns"
            assert result.stdout.strip() == '"default"'
        finally:
            os.unlink(script_path)
