"""Regression tests for command injection in the flux/core toolset.

Root cause pattern (same class as SEC-INJ-001, fixed in kubernetes.yaml):
tool parameters are sanitized with ``shlex.quote`` (see
``holmes.core.tools.sanitize``), which wraps a dangerous value in SINGLE quotes
(``$(id)`` -> ``'$(id)'``). That is only safe when the value lands in an
*unquoted* shell token. An earlier version of flux.yaml interpolated params
such as ``{{ name }}`` INSIDE double-quoted assignments (``NAME="{{ name }}"``)
and then re-parsed the result a second time via ``eval``, where the injected
single quotes are literal and ``$(...)`` command substitution stays ACTIVE.

These tests render the real toolset scripts the exact way ``YAMLTool`` does
(sanitize -> jinja render) and then execute the result under ``/bin/bash``
(matching the ``shell=True, executable="/bin/bash"`` production path). If any
parameter can smuggle command substitution through, an attacker marker file
would be created. The tests assert it never is.
"""

import os
import shutil
import subprocess
import tempfile

import pytest
from jinja2 import Template

from holmes.core.tools import sanitize
from holmes.plugins.toolsets import load_toolsets_from_file

FLUX_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "holmes",
    "plugins",
    "toolsets",
    "flux.yaml",
)


def _load_target_tools():
    toolsets = load_toolsets_from_file(FLUX_YAML, strict_check=False)
    tools = []
    for toolset in toolsets:
        if toolset.name != "flux/core":
            continue
        tools.extend(toolset.tools)
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
    """A PATH dir with a no-op flux so the scripts run to completion without
    touching a real cluster. Real coreutils stay in scope. The marker file
    can therefore only appear via injection."""
    p = os.path.join(dir_path, "flux")
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
    return list(tool.parameters.keys())


def _benign_value(tool, param):
    return "kustomizations" if param == "resource" else "default"


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
                params = {p: _benign_value(tool, p) for p in _string_param_names(tool)}
                params[param] = payload
                rendered = _render_tool(tool, params)
                _run_rendered(rendered, workdir)
                assert not os.path.exists(marker_path), (
                    f"COMMAND INJECTION in tool '{tool.name}' via param "
                    f"'{param}' with payload {payload!r}.\n"
                    f"Rendered script:\n{rendered}"
                )
                if os.path.exists(marker_path):
                    os.remove(marker_path)


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required for injection test"
)
def test_exact_name_param_poc():
    """The exact bug found in early review of this toolset: name='$(touch ...)'
    against flux_get must not create the marker file."""
    flux_get = next(t for t in TARGET_TOOLS if t.name == "flux_get")
    with tempfile.TemporaryDirectory() as workdir:
        marker_path = os.path.join(workdir, "PWNED_verify")
        params = {"resource": "all", "name": f"$(touch {marker_path})"}
        rendered = _render_tool(flux_get, params)
        _run_rendered(rendered, workdir)
        assert not os.path.exists(marker_path), (
            "Regression: name='$(...)' executed command substitution.\n"
            f"Rendered script:\n{rendered}"
        )


def test_positive_control_detects_injection():
    """Guard against a test that can never fail: the OLD vulnerable template
    pattern (param inside a double-quoted slot, then eval'd) MUST create the
    marker, proving the harness actually detects injection."""
    with tempfile.TemporaryDirectory() as workdir:
        marker_path = os.path.join(workdir, "PWNED_control")
        # Mirror the pre-fix vulnerable slot: shlex.quote'd value inside "..."
        vulnerable = 'NAME="' + sanitize(f"$(touch {marker_path})") + '"'
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
