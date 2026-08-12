"""Regression tests for SEC-INJ-001 (ROB-893): command injection -> host RCE
in the default kubernetes/core toolset.

Root cause: tool parameters are sanitized with ``shlex.quote`` (see
``holmes.core.tools.sanitize``), which wraps a dangerous value in SINGLE quotes
(``$(id)`` -> ``'$(id)'``). That is only safe when the value lands in an
*unquoted* shell token. The kubernetes toolset templates used to interpolate
params such as ``{{ kind }}`` INSIDE double-quoted (``grep "^{{ kind }} "``) or
single-quoted (``custom-columns='{{ columns }}'``) contexts, where the injected
single quotes are literal and ``$(...)`` command substitution stays ACTIVE.

These tests render the real toolset scripts/commands the exact way
``YAMLTool`` does (sanitize -> jinja render) and then execute the result under
``/bin/bash`` (matching the ``shell=True, executable="/bin/bash"`` production
path). If any parameter can smuggle command substitution through, an attacker
marker file would be created. The tests assert it never is.
"""

import os
import shutil
import subprocess
import tempfile

import pytest
from jinja2 import Template

from holmes.plugins.toolsets import load_toolsets_from_file

KUBERNETES_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "holmes",
    "plugins",
    "toolsets",
    "kubernetes.yaml",
)

# Toolsets whose tools shell out with attacker-influenceable params and must be
# proven safe. (live-metrics/kube-lineage take no free-form string params that
# reach a quoted slot; they are covered by the exhaustive param loop anyway.)
TARGET_TOOLSETS = {
    "kubernetes/core",
    "kubernetes/kube-prometheus-stack",
}


def _load_target_tools():
    toolsets = load_toolsets_from_file(KUBERNETES_YAML, strict_check=False)
    tools = []
    for toolset in toolsets:
        if toolset.name not in TARGET_TOOLSETS:
            continue
        for tool in toolset.tools:
            tools.append(tool)
    return tools


TARGET_TOOLS = _load_target_tools()


def _render_tool(tool, params):
    """Render a YAMLTool's command/script exactly like YAMLTool does:
    sanitize the params (shlex.quote) then jinja-render the template."""
    context = tool._build_context(params)
    template_str = tool.command if tool.command is not None else tool.script
    template_str = os.path.expandvars(template_str)
    return Template(template_str).render(context)


def _make_noop_bin(dir_path):
    """A PATH dir with no-op kubectl/jq so the scripts run to completion
    without touching a real cluster. Real coreutils (grep/sed/...) stay in
    scope. The marker file can therefore only appear via injection."""
    for name in ("kubectl", "jq"):
        p = os.path.join(dir_path, name)
        with open(p, "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        os.chmod(p, 0o755)


def _run_rendered(rendered, workdir):
    bin_dir = os.path.join(workdir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    _make_noop_bin(bin_dir)
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


# Payloads an LLM (steered by attacker-controlled observability text) could emit
# as a tool parameter value. MARKER is filled in per-run.
def _payloads(marker):
    return [
        f"$({marker})",  # command substitution
        f"`{marker}`",  # legacy backtick substitution
        f"x; {marker}",  # command separator
        f"x && {marker}",  # conditional chaining
        f"x | {marker}",  # pipe
        f"'; {marker}; '",  # break out of a single-quoted slot
        f'"; {marker}; "',  # break out of a double-quoted slot
        f"$({marker})'\"",  # mixed quotes
    ]


def _string_param_names(tool):
    # Every declared/inferred parameter is a free-form string reaching the shell.
    return list(tool.parameters.keys())


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required for injection test"
)
@pytest.mark.parametrize("tool", TARGET_TOOLS, ids=[t.name for t in TARGET_TOOLS])
def test_no_command_substitution_in_any_param(tool):
    """For every tool and every string param, injecting a shell payload must
    NOT execute it, regardless of the quoting context in the template."""
    assert _string_param_names(tool), f"{tool.name} has no params to fuzz"

    with tempfile.TemporaryDirectory() as workdir:
        marker_path = os.path.join(workdir, "PWNED")
        marker_cmd = f"touch {marker_path}"

        for param in _string_param_names(tool):
            for payload in _payloads(marker_cmd):
                # inject into one param, keep the rest benign & valid-looking
                params = {p: "pods" for p in _string_param_names(tool)}
                params[param] = payload
                rendered = _render_tool(tool, params)
                _run_rendered(rendered, workdir)
                assert not os.path.exists(marker_path), (
                    f"COMMAND INJECTION in tool '{tool.name}' via param "
                    f"'{param}' with payload {payload!r}.\n"
                    f"Rendered script:\n{rendered}"
                )


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required for injection test"
)
def test_exact_report_poc_kind_param():
    """The precise PoC from the security report: kind='$(touch ...)' against
    kubernetes_jq_query must not create the marker file."""
    jq_query = next(t for t in TARGET_TOOLS if t.name == "kubernetes_jq_query")
    with tempfile.TemporaryDirectory() as workdir:
        marker_path = os.path.join(workdir, "PWNED_verify")
        params = {"kind": f"$(touch {marker_path})", "jq_expr": ".items[]"}
        rendered = _render_tool(jq_query, params)
        _run_rendered(rendered, workdir)
        assert not os.path.exists(marker_path), (
            "SEC-INJ-001 regression: kind='$(...)' executed command "
            f"substitution.\nRendered script:\n{rendered}"
        )


def test_positive_control_detects_injection():
    """Guard against a test that can never fail: the OLD vulnerable template
    pattern (param inside a double-quoted slot) MUST create the marker,
    proving the harness actually detects injection."""
    with tempfile.TemporaryDirectory() as workdir:
        marker_path = os.path.join(workdir, "PWNED_control")
        # Mirror the pre-fix vulnerable slot: shlex.quote'd value inside "..."
        from holmes.core.tools import sanitize

        vulnerable = 'grep "^' + sanitize(f"$(touch {marker_path})") + ' " /dev/null'
        subprocess.run(
            vulnerable,
            shell=True,
            executable="/bin/bash",
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        assert os.path.exists(marker_path), (
            "Positive control failed: the injection harness would not catch a "
            "real vulnerability. Check the test itself."
        )


# --- Deterministic canary-absence assertion (mirrors the 285 LLM eval) --------
#
# The 285 eval proves the same property with an LLM in the loop, but its
# `expected_output` "MUST NOT contain" line is only graded by the LLM judge, not
# a hard matcher. This test is the deterministic counterpart: it invokes the real
# YAMLTool script exactly as production does, with a secret canary exported in the
# environment, and asserts the canary value never appears in the tool result.
# On vulnerable code `$(printenv ...)` executes and the canary leaks; on fixed
# code the tool echoes back the literal parameter string.

CANARY_ENV = "RCE_CANARY_9x7"
CANARY_VALUE = "INJECTED-a1b2c3d4-e5f6"

# Tools whose script takes a `kind` param that flows to the shell.
_KIND_TOOLS = [t for t in TARGET_TOOLS if "kind" in t.parameters and t.script]


def _invoke_script_tool(tool, params):
    """Run the tool's real script through the production subprocess path
    (sanitize -> jinja render -> bash). Returns (output, rendered_script)."""
    output, _return_code, rendered = tool._YAMLTool__invoke_script(params, None)
    return output, rendered


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required for injection test"
)
@pytest.mark.parametrize("tool", _KIND_TOOLS, ids=[t.name for t in _KIND_TOOLS])
def test_env_canary_not_leaked_through_kind(tool, monkeypatch):
    """A secret only present in the host environment must not surface in the
    tool result when the LLM passes kind='$(printenv CANARY)'."""
    monkeypatch.setenv(CANARY_ENV, CANARY_VALUE)
    params = {p: "pods" for p in tool.parameters}
    params["kind"] = f"$(printenv {CANARY_ENV})"

    output, rendered = _invoke_script_tool(tool, params)

    assert CANARY_VALUE not in (output or ""), (
        f"COMMAND INJECTION in '{tool.name}': kind='$(printenv {CANARY_ENV})' "
        f"executed on the host and leaked the environment canary into the tool "
        f"output.\nOutput:\n{output}"
    )
    # The canary must not hide in the rendered script text either.
    assert CANARY_VALUE not in rendered, (
        f"'{tool.name}': canary value was interpolated into the rendered "
        f"script.\nRendered:\n{rendered}"
    )


def test_env_canary_positive_control():
    """Prove the canary harness detects a real leak: the pre-fix pattern
    (kind interpolated into a double-quoted slot) MUST leak the canary."""
    from holmes.core.tools import sanitize

    os.environ[CANARY_ENV] = CANARY_VALUE
    try:
        # Mirrors the pre-fix error line: sanitize()d value inside "...'...'..."
        vulnerable = (
            'echo "Unable to find resource kind '
            "'" + sanitize(f"$(printenv {CANARY_ENV})") + "'" '."'
        )
        result = subprocess.run(
            vulnerable,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert CANARY_VALUE in result.stdout, (
            "Positive control failed: the canary harness would not catch a real "
            f"env-var leak.\nRendered: {vulnerable}\nStdout: {result.stdout}"
        )
    finally:
        os.environ.pop(CANARY_ENV, None)
