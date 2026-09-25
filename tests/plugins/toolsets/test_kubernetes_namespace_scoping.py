"""Tests for namespace scoping, output budgeting, and error surfacing in the
kubernetes query tools (kubernetes_tabular_query, kubernetes_jq_query,
kubernetes_count).

Covers https://github.com/HolmesGPT/holmesgpt/issues/2438
(kubernetes_tabular_query always queries all namespaces, OOMs kubectl on large
clusters, and reports the crash as a successful empty result) and
https://github.com/HolmesGPT/holmesgpt/issues/2427 (the three query tools are
cluster-wide with no namespace parameter).

The tests render the real toolset scripts the exact way ``YAMLTool`` does
(sanitize -> jinja render) and execute them against a fake ``kubectl`` on PATH,
so they verify the generated shell behavior without a live cluster.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest
from jinja2 import Template

from holmes.core.tools import StructuredToolResultStatus, sanitize
from holmes.plugins.toolsets import load_toolsets_from_file

KUBERNETES_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "holmes",
    "plugins",
    "toolsets",
    "kubernetes.yaml",
)

FAKE_KUBECTL = """#!/bin/bash
# Fake kubectl for namespace-scoping tests.
# Logs every invocation's argv to $KUBECTL_ARGS_LOG, then dispatches.
echo "$@" >> "$KUBECTL_ARGS_LOG"
if [[ "$*" == *"api-resources"* ]]; then
  echo "pods pod v1 true Pod"
  echo "nodes node v1 false Node"
  exit 0
fi
if [[ "$*" == *"--raw"* ]]; then
  echo '{"items": [{"metadata": {"name": "pod-a", "namespace": "team-a"}}, {"metadata": {"name": "pod-b", "namespace": "team-a"}}], "metadata": {}}'
  exit 0
fi
if [ -n "$FAKE_FAIL" ]; then
  echo "runtime: out of memory: cannot allocate 8388608-byte block" >&2
  echo "fatal error: out of memory" >&2
  exit 1
fi
# tabular output: header + $FAKE_ROWS data rows
echo "NAME   STATUS"
for i in $(seq 1 "${FAKE_ROWS:-2}"); do
  echo "pod-$i   Running"
done
exit 0
"""


def _load_tools():
    toolsets = load_toolsets_from_file(KUBERNETES_YAML, strict_check=False)
    core = next(ts for ts in toolsets if ts.name == "kubernetes/core")
    return {t.name: t for t in core.tools}


TOOLS = _load_tools()


def _render_tool(tool, params):
    """Render a YAMLTool's script exactly like YAMLTool does."""
    context = tool._build_context(params)
    template_str = os.path.expandvars(tool.script)
    return Template(template_str).render(context)


@pytest.fixture()
def fake_kubectl_env():
    with tempfile.TemporaryDirectory() as workdir:
        bin_dir = os.path.join(workdir, "bin")
        os.makedirs(bin_dir)
        kubectl_path = os.path.join(bin_dir, "kubectl")
        with open(kubectl_path, "w") as f:
            f.write(FAKE_KUBECTL)
        os.chmod(kubectl_path, 0o755)
        args_log = os.path.join(workdir, "kubectl_args.log")
        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env["KUBECTL_ARGS_LOG"] = args_log
        yield env, args_log, workdir


def _run_script(rendered, env, workdir):
    return subprocess.run(
        rendered,
        shell=True,
        executable="/bin/bash",
        cwd=workdir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def _logged_kubectl_calls(args_log):
    with open(args_log) as f:
        return [line.strip() for line in f if line.strip()]


# --- kubernetes_tabular_query -------------------------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_scopes_to_namespace(fake_kubectl_env):
    env, args_log, workdir = fake_kubectl_env
    tool = TOOLS["kubernetes_tabular_query"]
    rendered = _render_tool(
        tool,
        {"kind": "pods", "columns": "NAME:.metadata.name", "namespace": "team-a"},
    )
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    calls = _logged_kubectl_calls(args_log)
    get_calls = [c for c in calls if c.startswith("get ")]
    assert get_calls, "expected a kubectl get invocation"
    assert any("-n team-a" in c for c in get_calls)
    assert not any("--all-namespaces" in c for c in get_calls)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_defaults_to_all_namespaces(fake_kubectl_env):
    env, args_log, workdir = fake_kubectl_env
    tool = TOOLS["kubernetes_tabular_query"]
    rendered = _render_tool(tool, {"kind": "pods", "columns": "NAME:.metadata.name"})
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    calls = _logged_kubectl_calls(args_log)
    get_calls = [c for c in calls if c.startswith("get ")]
    assert any("--all-namespaces" in c for c in get_calls)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_kubectl_failure_is_an_error_not_empty_success(
    fake_kubectl_env,
):
    """Regression test for #2438: a kubectl crash (e.g. OOM) must surface as a
    non-zero exit so the framework reports ERROR, not SUCCESS with zero rows."""
    env, args_log, workdir = fake_kubectl_env
    env["FAKE_FAIL"] = "1"
    tool = TOOLS["kubernetes_tabular_query"]
    rendered = _render_tool(tool, {"kind": "pods", "columns": "NAME:.metadata.name"})
    result = _run_script(rendered, env, workdir)
    assert result.returncode != 0, "kubectl failure must not exit 0"
    payload = json.loads(result.stdout)
    assert payload["count"] == 0
    assert payload["rows"] == []
    assert "out of memory" in payload["stderr"]
    # The framework maps a non-zero exit to ERROR (never silent SUCCESS)
    assert tool._get_status(result.returncode, result.stdout) == (
        StructuredToolResultStatus.ERROR
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_truncates_oversized_results(fake_kubectl_env):
    env, args_log, workdir = fake_kubectl_env
    env["FAKE_ROWS"] = "5"
    tool = TOOLS["kubernetes_tabular_query"]
    rendered = _render_tool(
        tool,
        {
            "kind": "pods",
            "columns": "NAME:.metadata.name",
            "max_rows": 3,
        },
    )
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 3
    assert len(payload["rows"]) == 3
    assert payload["truncated"] is True
    assert "truncated to 3 of 5 rows" in payload["stderr"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_no_truncation_under_limit(fake_kubectl_env):
    env, args_log, workdir = fake_kubectl_env
    env["FAKE_ROWS"] = "2"
    tool = TOOLS["kubernetes_tabular_query"]
    rendered = _render_tool(
        tool,
        {
            "kind": "pods",
            "columns": "NAME:.metadata.name",
            "max_rows": 10,
        },
    )
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert payload["truncated"] is False
    assert payload["stderr"] is None


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_default_max_rows_when_omitted(fake_kubectl_env):
    """Omitting max_rows must not break rendering (Jinja renders it empty) and
    the script must fall back to the documented default of 1000."""
    env, args_log, workdir = fake_kubectl_env
    env["FAKE_ROWS"] = "4"
    tool = TOOLS["kubernetes_tabular_query"]
    rendered = _render_tool(tool, {"kind": "pods", "columns": "NAME:.metadata.name"})
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 4
    assert payload["truncated"] is False


# --- kubernetes_jq_query / kubernetes_count -----------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize("tool_name", ["kubernetes_jq_query", "kubernetes_count"])
def test_api_tools_scope_namespaced_kind_to_namespace(fake_kubectl_env, tool_name):
    env, args_log, workdir = fake_kubectl_env
    tool = TOOLS[tool_name]
    rendered = _render_tool(
        tool,
        {"kind": "pods", "jq_expr": ".items[]", "namespace": "team-a"},
    )
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    calls = _logged_kubectl_calls(args_log)
    raw_calls = [c for c in calls if "--raw" in c]
    assert raw_calls, "expected a kubectl get --raw invocation"
    assert any("/api/v1/namespaces/team-a/pods" in c for c in raw_calls)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize("tool_name", ["kubernetes_jq_query", "kubernetes_count"])
def test_api_tools_stay_cluster_wide_without_namespace(fake_kubectl_env, tool_name):
    env, args_log, workdir = fake_kubectl_env
    tool = TOOLS[tool_name]
    rendered = _render_tool(tool, {"kind": "pods", "jq_expr": ".items[]"})
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    calls = _logged_kubectl_calls(args_log)
    raw_calls = [c for c in calls if "--raw" in c]
    assert any("/api/v1/pods?limit=" in c for c in raw_calls)
    assert not any("/namespaces/" in c for c in raw_calls)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize("tool_name", ["kubernetes_jq_query", "kubernetes_count"])
def test_api_tools_ignore_namespace_for_cluster_scoped_kind(
    fake_kubectl_env, tool_name
):
    """A namespace must not produce a bogus /namespaces/<ns>/nodes API path for
    cluster-scoped kinds - the query stays cluster-wide."""
    env, args_log, workdir = fake_kubectl_env
    tool = TOOLS[tool_name]
    rendered = _render_tool(
        tool,
        {"kind": "nodes", "jq_expr": ".items[]", "namespace": "team-a"},
    )
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    calls = _logged_kubectl_calls(args_log)
    raw_calls = [c for c in calls if "--raw" in c]
    assert any("/api/v1/nodes?limit=" in c for c in raw_calls)
    assert not any("/namespaces/" in c for c in raw_calls)


# --- Jinja2 builtin shadowing regression ---------------------------------------
# Jinja2 exposes a builtin `namespace` class in every template's globals. If the
# `namespace` param is omitted from the tool call, `{{ namespace }}` would render
# as "<class 'jinja2.utils.Namespace'>" and `{% if namespace %}` would be truthy
# - producing `kubectl get pods -n "<class 'jinja2.utils.Namespace'>"`. The
# templates must treat a missing namespace as empty.


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize(
    "tool_name", ["kubernetes_tabular_query", "kubernetes_jq_query", "kubernetes_count"]
)
def test_missing_namespace_does_not_leak_jinja_builtin(tool_name, fake_kubectl_env):
    env, args_log, workdir = fake_kubectl_env
    tool = TOOLS[tool_name]
    params = {"kind": "pods", "jq_expr": ".items[]", "columns": "NAME:.metadata.name"}
    rendered = _render_tool(tool, params)
    assert "jinja2.utils.Namespace" not in rendered
    result = _run_script(rendered, env, workdir)
    assert result.returncode == 0, result.stderr
    calls = _logged_kubectl_calls(args_log)
    assert not any("jinja2" in c for c in calls)
    assert any("--all-namespaces" in c or "/api/v1/pods" in c for c in calls)
    one_liner = tool.get_parameterized_one_liner(params)
    assert "jinja2" not in one_liner
    assert "--all-namespaces" in one_liner


# --- end-to-end: the exact #2438 scenario through tool.invoke() -----------------
# A kubectl OOM crash must come back as StructuredToolResultStatus.ERROR (with
# the framework's OOM hint advising a narrower query), never as SUCCESS with
# zero rows.


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_tabular_query_oom_crash_surfaces_as_error_end_to_end(fake_kubectl_env):
    from tests.conftest import create_mock_tool_invoke_context

    env, args_log, workdir = fake_kubectl_env
    # Run the real subprocess path (no mocks) with the fake kubectl on PATH.
    old_path = os.environ.get("PATH", "")
    old_fake_fail = os.environ.get("FAKE_FAIL")
    os.environ["PATH"] = env["PATH"]
    os.environ["FAKE_FAIL"] = "1"
    try:
        tool = TOOLS["kubernetes_tabular_query"]
        context = create_mock_tool_invoke_context(user_approved=True)
        result = tool.invoke(
            {"kind": "pods", "columns": "NAME:.metadata.name"}, context
        )
    finally:
        os.environ["PATH"] = old_path
        if old_fake_fail is None:
            os.environ.pop("FAKE_FAIL", None)
        else:
            os.environ["FAKE_FAIL"] = old_fake_fail

    assert result.status == StructuredToolResultStatus.ERROR, (
        f"#2438 regression: kubectl crash reported as {result.status}"
    )
    assert result.error is not None and "return code" in result.error
    # The framework prepends its OOM hint (with namespace guidance) to the
    # tool output on OOM crashes; the error JSON follows it.
    assert "[OOM]" in result.data
    assert "filter by namespace" in result.data
    payload = json.loads(result.data[result.data.index("{"):])
    assert payload["count"] == 0
    assert payload["rows"] == []
    assert "out of memory" in payload["stderr"]


# --- user_description honesty (#2427: templates must not look namespaced) ------


def test_user_descriptions_reflect_namespace_scope():
    tabular = TOOLS["kubernetes_tabular_query"]
    jq = TOOLS["kubernetes_jq_query"]
    count = TOOLS["kubernetes_count"]

    assert "--all-namespaces" in tabular.get_parameterized_one_liner(
        {"kind": "pods", "columns": "NAME:.metadata.name"}
    )
    assert "-n team-a" in tabular.get_parameterized_one_liner(
        {"kind": "pods", "columns": "NAME:.metadata.name", "namespace": "team-a"}
    )
    assert "--all-namespaces" in jq.get_parameterized_one_liner(
        {"kind": "pods", "jq_expr": ".items[]"}
    )
    assert "-n team-a" in jq.get_parameterized_one_liner(
        {"kind": "pods", "jq_expr": ".items[]", "namespace": "team-a"}
    )
    assert "--all-namespaces" in count.get_parameterized_one_liner(
        {"kind": "pods", "jq_expr": ".items[]"}
    )
    assert "-n team-a" in count.get_parameterized_one_liner(
        {"kind": "pods", "jq_expr": ".items[]", "namespace": "team-a"}
    )


def test_namespace_params_are_optional_in_schema():
    for tool_name in (
        "kubernetes_tabular_query",
        "kubernetes_jq_query",
        "kubernetes_count",
    ):
        tool = TOOLS[tool_name]
        assert "namespace" in tool.parameters
        assert tool.parameters["namespace"].required is False
    assert TOOLS["kubernetes_tabular_query"].parameters["max_rows"].required is False


def test_namespace_param_is_injection_safe():
    """A hostile namespace value must be neutralized by sanitize(), like every
    other string param (mirrors the ROB-893 hardening)."""
    tool = TOOLS["kubernetes_tabular_query"]
    context = tool._build_context(
        {
            "kind": "pods",
            "columns": "NAME:.metadata.name",
            "namespace": "$(touch /tmp/pwned-ns)",
        }
    )
    rendered = Template(os.path.expandvars(tool.script)).render(context)
    with tempfile.TemporaryDirectory() as workdir:
        bin_dir = os.path.join(workdir, "bin")
        os.makedirs(bin_dir)
        kubectl_path = os.path.join(bin_dir, "kubectl")
        with open(kubectl_path, "w") as f:
            f.write("#!/bin/bash\necho 'NAME   STATUS'\nexit 0\n")
        os.chmod(kubectl_path, 0o755)
        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        subprocess.run(
            rendered,
            shell=True,
            executable="/bin/bash",
            cwd=workdir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        assert not os.path.exists("/tmp/pwned-ns")
