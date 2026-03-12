"""
End-to-end tests for jq expression handling in kubernetes tools.

These tests invoke the real YAMLTool code path (sanitize_params → Jinja2 render →
write temp .sh file → subprocess execution) to verify that complex jq expressions
with shell-hostile characters are executed correctly via file-based jq -f.

RED/GREEN contract:
- TestYAMLToolJqExecution: Behavioral tests that run through the real YAMLTool
  __invoke_script path with complex jq expressions and verify correct output.
- TestRenderedScriptIsolatesJqExpression: Renders the ACTUAL kubernetes.yaml
  template and asserts the jq invocation line does NOT contain the literal
  expression (it should be in a file, not inline). These tests go RED if
  someone reverts kubernetes.yaml to the old inline `jq -c {{ jq_expr }}` pattern.
"""

import json
import os
import re

import pytest

from holmes.core.tools import YAMLTool, sanitize_params
from holmes.plugins.toolsets import load_toolsets_from_file
from jinja2 import Template

# Path to the kubernetes.yaml toolset
KUBERNETES_YAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..",
    "holmes", "plugins", "toolsets", "kubernetes.yaml",
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

# JSON test data that the above expression should match against
TEST_DATA = {
    "items": [
        {
            "metadata": {"name": "app-deploy", "namespace": "production"},
            "spec": {
                "containers": [
                    {
                        "env": [
                            {"name": "DB_PASSWORD", "value": "s3cret"},
                            {"name": "SERVICE_PORT", "value": "8080"},
                            {"name": "APP_NAME", "value": "myapp"},
                        ]
                    }
                ]
            },
        },
        {
            "metadata": {"name": "sys-deploy", "namespace": "kube-system"},
            "spec": {
                "containers": [
                    {
                        "env": [
                            {"name": "SECRET_KEY", "value": "hidden"},
                        ]
                    }
                ]
            },
        },
    ]
}

TEST_JSON = json.dumps(TEST_DATA)


def _load_tool(tool_name: str):
    """Load a tool from the actual kubernetes.yaml file."""
    toolsets = load_toolsets_from_file(KUBERNETES_YAML_PATH)
    kubernetes_core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
    return next(t for t in kubernetes_core.tools if t.name == tool_name)


def _render_tool_script(tool_name: str, kind: str, jq_expr: str) -> str:
    """Render a tool's script template the same way tools.py does."""
    tool = _load_tool(tool_name)
    params = sanitize_params({"kind": kind, "jq_expr": jq_expr})
    context = {**params, "env": os.environ}
    expanded = os.path.expandvars(tool.script)
    template = Template(expanded)
    return template.render(context)


def _invoke_tool_script(script_template: str, jq_expr: str) -> tuple:
    """
    Invoke a YAMLTool script through the real execution path.

    Uses the same code path as production: sanitize_params → Jinja2 render →
    write to temp .sh file → subprocess.run with shell=True.

    Returns (stdout, return_code, rendered_script).
    """
    tool = YAMLTool(
        name="test_jq_tool",
        description="Test tool for jq expression handling",
        script=script_template,
    )
    # Call the private __invoke_script method through name mangling.
    # This exercises: sanitize_params, _build_context, Template.render,
    # tempfile write, chmod, __execute_subprocess
    output, return_code, rendered_script = tool._YAMLTool__invoke_script(
        {"jq_expr": jq_expr}
    )
    return output, return_code, rendered_script


def _make_file_based_script(test_json: str) -> str:
    """Create a script using the file-based jq -f pattern (the fix)."""
    escaped_json = test_json.replace("'", "'\\''")
    return (
        "#!/bin/bash\n"
        "jq_file=$(mktemp)\n"
        "printf '%s' {{ jq_expr }} > \"$jq_file\"\n"
        f"echo '{escaped_json}' | jq -c -f \"$jq_file\"\n"
        "rc=$?; rm -f \"$jq_file\"; exit $rc\n"
    )


class TestYAMLToolJqExecution:
    """
    Behavioral GREEN tests: exercise the real YAMLTool.__invoke_script code path
    with complex jq expressions and verify correct output.
    """

    def test_complex_expression_with_pipes_and_regex(self):
        """Complex jq with pipes, regex alternation, semicolons produces correct output."""
        script = _make_file_based_script(TEST_JSON)
        output, return_code, _ = _invoke_tool_script(script, COMPLEX_JQ_EXPR)

        assert return_code == 0, f"Script failed (rc={return_code}): {output}"
        assert "command not found" not in output
        # Should find DB_PASSWORD (matches PASSWORD pattern, in production ns,
        # not a SERVICE_PORT-style name)
        assert '"DB_PASSWORD"' in output
        # SERVICE_PORT should be excluded by the negative filter
        assert "SERVICE_PORT" not in output
        # kube-system namespace items should be excluded by namespace filter
        assert "SECRET_KEY" not in output

    def test_dollar_sign_anchors_not_expanded(self):
        """$ in regex anchors must not be expanded as shell variables."""
        test_data = json.dumps({
            "items": [
                {"name": "default", "metadata": {"name": "exact"}},
                {"name": "default-ns", "metadata": {"name": "prefix-only"}},
            ]
        })
        expr = '.items[] | select(.name | test("^default$")) | .metadata.name'
        script = _make_file_based_script(test_data)
        output, return_code, _ = _invoke_tool_script(script, expr)

        assert return_code == 0, f"Script failed: {output}"
        assert '"exact"' in output
        assert "prefix-only" not in output

    def test_semicolons_in_regex_flags(self):
        """Semicolons in jq test() flags (e.g. test("x"; "i")) must work."""
        test_data = json.dumps([
            {"name": "hello_world", "value": "found"},
            {"name": "goodbye", "value": "skip"},
        ])
        expr = '.[] | select(.name | test("HELLO"; "i")) | .value'
        script = _make_file_based_script(test_data)
        output, return_code, _ = _invoke_tool_script(script, expr)

        assert return_code == 0, f"Script failed: {output}"
        assert '"found"' in output
        assert "skip" not in output

    def test_expression_with_double_pipe_null_coalescing(self):
        """jq's // (alternative operator) must not be treated as shell OR."""
        test_data = json.dumps({"items": [
            {"env": [{"name": "A"}]},
            {"env": None},
        ]})
        expr = '.items[] | .env // [] | .[] | .name'
        script = _make_file_based_script(test_data)
        output, return_code, _ = _invoke_tool_script(script, expr)

        assert return_code == 0, f"Script failed: {output}"
        assert '"A"' in output


class TestRenderedScriptIsolatesJqExpression:
    """
    RED tests if the fix is reverted: render the ACTUAL kubernetes.yaml templates
    and verify the jq invocation line does NOT contain the literal expression.

    With the file-based fix:  `jq -c -f "$jq_file"` → expression NOT on this line ✓
    With old inline pattern:  `jq -c '.items[] | ...'` → expression IS on this line ✗

    These tests go RED if someone changes kubernetes.yaml back to inline jq.
    """

    def test_jq_query_jq_invocation_does_not_contain_expression(self):
        """The jq invocation line in kubernetes_jq_query must not embed the expression."""
        rendered = _render_tool_script("kubernetes_jq_query", "deployments", COMPLEX_JQ_EXPR)

        for line in rendered.split("\n"):
            stripped = line.strip()
            # Find lines that invoke jq with the MATCHES variable assignment
            if re.match(r"MATCHES=\$\(.*jq\s", stripped):
                assert "PASSWORD" not in stripped, (
                    f"jq invocation embeds the expression inline (should use -f file): "
                    f"{stripped}"
                )
                assert "-f" in stripped, (
                    f"jq invocation must use -f flag to read from file: {stripped}"
                )

    def test_count_jq_invocation_does_not_contain_expression(self):
        """The jq invocation line in kubernetes_count must not embed the expression."""
        rendered = _render_tool_script("kubernetes_count", "pods", COMPLEX_JQ_EXPR)

        for line in rendered.split("\n"):
            stripped = line.strip()
            if re.match(r"BATCH_MATCHES=\$\(.*jq\s", stripped):
                assert "PASSWORD" not in stripped, (
                    f"jq invocation embeds the expression inline (should use -f file): "
                    f"{stripped}"
                )
                assert "-f" in stripped, (
                    f"jq invocation must use -f flag to read from file: {stripped}"
                )

    def test_jq_query_expression_written_to_file_via_printf(self):
        """The rendered script must write the expression to a temp file."""
        rendered = _render_tool_script("kubernetes_jq_query", "deployments", COMPLEX_JQ_EXPR)

        # The expression must appear on a printf line writing to jq_file
        printf_lines = [
            line.strip() for line in rendered.split("\n")
            if "printf" in line and "jq_file" in line
        ]
        assert len(printf_lines) == 1, (
            f"Expected exactly one printf-to-jq_file line, got {len(printf_lines)}"
        )
        # The printf line must contain the expression (this is where it belongs)
        assert "PASSWORD" in printf_lines[0], (
            "The jq expression should be on the printf line, not the jq invocation"
        )

    def test_count_expression_written_to_file_via_printf(self):
        """The rendered kubernetes_count script must write expression to a temp file."""
        rendered = _render_tool_script("kubernetes_count", "pods", COMPLEX_JQ_EXPR)

        printf_lines = [
            line.strip() for line in rendered.split("\n")
            if "printf" in line and "jq_file" in line
        ]
        assert len(printf_lines) == 1
        assert "PASSWORD" in printf_lines[0]


class TestTempFileCleanup:
    """Verify $jq_file is cleaned up in ALL exit paths."""

    @pytest.fixture()
    def kubernetes_tools(self):
        toolsets = load_toolsets_from_file(KUBERNETES_YAML_PATH)
        core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
        return {t.name: t for t in core.tools}

    @pytest.mark.parametrize("tool_name", ["kubernetes_jq_query", "kubernetes_count"])
    def test_every_err_file_cleanup_includes_jq_file(self, kubernetes_tools, tool_name):
        """Every rm -f $err_file line must also remove $jq_file."""
        tool = kubernetes_tools[tool_name]
        for i, line in enumerate(tool.script.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("rm -f") and "$err_file" in stripped:
                assert "$jq_file" in stripped, (
                    f"{tool_name} line {i}: cleanup removes $err_file but not $jq_file: "
                    f"{stripped}"
                )
