"""Systemic regression tests for the SEC-INJ command-injection class.

Background (ROB-893 / SEC-INJ-001): built-in YAML toolsets run their
``command``/``script`` with ``shell=True`` after Jinja-rendering LLM-supplied
parameters. Parameters are sanitized with ``shlex.quote`` (see
``holmes.core.tools.sanitize``), which wraps a dangerous value in SINGLE quotes
(``$(id)`` -> ``'$(id)'``). That is safe ONLY in an *unquoted* shell token. When
a ``{{ param }}`` is interpolated INSIDE a double- or single-quoted slot, the
injected quote closes the surrounding quote and ``$(...)`` command substitution
stays active -> arbitrary code runs on the Holmes host.

The kubernetes toolset was the first fix; the same class was then found in
``slab``, ``kubevela``, ``inspektor_gadget`` and ``aks``. These tests guard the
WHOLE class across every built-in YAML toolset so it can't silently return:

1. ``test_no_param_interpolated_inside_quotes`` - a fast static scan asserting no
   executed ``command``/``script`` places a ``{{ param }}`` inside a quoted
   shell context.
2. ``test_no_command_substitution_executes`` - the behavioural proof: render each
   tool exactly as ``YAMLTool`` does and execute it under ``/bin/bash`` with a
   battery of injection payloads in each parameter, asserting nothing runs.
"""

import glob
import os
import re
import shutil
import subprocess
import tempfile

import pytest
from jinja2 import Template

from holmes.plugins.toolsets import load_toolsets_from_file

TOOLSET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "holmes",
    "plugins",
    "toolsets",
)

YAML_FILES = sorted(
    glob.glob(os.path.join(TOOLSET_DIR, "**", "*.yaml"), recursive=True)
)

# External binaries the rendered commands invoke. Stubbed to no-ops so the tests
# never touch a real cluster / cloud / network. (Command substitution injected
# into a parameter fires during shell expansion regardless of whether these
# exist, so stubbing only prevents side effects - it never hides an injection.)
# The list is validated to cover every external CLI actually invoked by a
# built-in toolset command/script (coreutils are intentionally left real).
STUB_BINS = [
    "kubectl", "oc", "az", "gcloud", "aws", "vela", "ig", "helm", "curl",
    "wget", "dig", "nslookup", "host", "tcpdump", "jq", "kube-lineage",
    "kubectl-lineage", "docker", "nc", "ping", "psql", "mysql", "argocd",
    "cilium", "hubble", "timeout",
]


def _load_yaml_tools():
    """Discover every executable (command/script) tool across all built-in
    YAML toolsets. Returns (tools, load_failures) so a YAML that stops parsing
    is reported rather than silently dropped."""
    tools = []
    failures = []
    for f in YAML_FILES:
        try:
            toolsets = load_toolsets_from_file(f, strict_check=False)
        except Exception as e:  # noqa: BLE001 - report the failure, don't hide it
            failures.append((os.path.basename(f), repr(e)))
            continue
        for toolset in toolsets:
            for tool in getattr(toolset, "tools", None) or []:
                if getattr(tool, "command", None) or getattr(tool, "script", None):
                    tools.append((os.path.basename(f), toolset.name, tool))
    return tools, failures


ALL_TOOLS, LOAD_FAILURES = _load_yaml_tools()
TOOL_IDS = [f"{fname}:{tool.name}" for fname, _ts, tool in ALL_TOOLS]


def _template_of(tool):
    """Return the tool's executed template (command takes precedence, else script)."""
    return tool.command if tool.command is not None else tool.script


# --- 1. Static scan: no {{ param }} inside a quoted shell context ------------

def _inside_quote_flags(text):
    """Whole-string bash-ish quote tracker (handles multi-line scripts).
    Returns a list[bool] marking indices that fall inside a '...' or "..."
    region. Quote characters themselves are boundaries (outside). Shell
    comments (an unquoted ``#`` at the start of a word to end-of-line) are
    skipped so apostrophes in comments don't look like open quotes."""
    inside = [False] * len(text)
    st = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if st is None:
            if c == "#" and (i == 0 or text[i - 1] in " \t\n"):
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if c in ("'", '"'):
                st = c
            i += 1
        else:
            if c == st:
                st = None
            else:
                inside[i] = True
            i += 1
    return inside


@pytest.mark.parametrize(
    "entry", ALL_TOOLS, ids=TOOL_IDS or ["none"]
)
def test_no_param_interpolated_inside_quotes(entry):
    """No executed command/script may place a Jinja {{ expression }} inside a
    quoted shell context - that is the exact shape that defeats shlex.quote."""
    fname, ts_name, tool = entry
    template = _template_of(tool)
    inside = _inside_quote_flags(template)
    bad = []
    for m in re.finditer(r"\{\{.*?\}\}", template, flags=re.DOTALL):
        if m.start() < len(inside) and inside[m.start()]:
            snippet = template[max(0, m.start() - 30): m.end() + 5].replace("\n", " ")
            bad.append(f"{m.group(0)}  (…{snippet}…)")
    assert not bad, (
        f"{fname} :: {tool.name}: parameter placeholder(s) sit inside a quoted "
        f"shell context - shlex.quote does NOT protect there. Assign to a shell "
        f"variable in an unquoted slot and reference it as \"$VAR\" instead.\n"
        + "\n".join(bad)
    )


# --- 2. Behavioural proof: nothing executes when a param carries a payload ---

def _make_stub_bin(workdir):
    """Create a bin dir of no-op stubs for every external CLI in STUB_BINS,
    to be prepended to PATH so the behavioural test never hits a real
    cluster/cloud/network. Returns the bin dir path."""
    bin_dir = os.path.join(workdir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for name in STUB_BINS:
        p = os.path.join(bin_dir, name)
        with open(p, "w") as fh:
            fh.write("#!/bin/bash\nexit 0\n")
        os.chmod(p, 0o755)
    return bin_dir


def _payloads(marker_cmd):
    """Shell-injection payloads an LLM could smuggle through a parameter; each
    runs marker_cmd if the value is not properly isolated from the shell."""
    return [
        f"$({marker_cmd})",
        f"`{marker_cmd}`",
        f"x; {marker_cmd}",
        f"x && {marker_cmd}",
        f"x | {marker_cmd}",
        f"'; {marker_cmd}; '",
        f'"; {marker_cmd}; "',
    ]


def _render(tool, params):
    """Render a tool's template exactly as YAMLTool does at runtime: sanitize
    params (shlex.quote via _build_context) -> expandvars -> jinja render."""
    context = tool._build_context(params)
    return Template(os.path.expandvars(_template_of(tool))).render(context)


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash required for injection test"
)
@pytest.mark.parametrize("entry", ALL_TOOLS, ids=TOOL_IDS or ["none"])
def test_no_command_substitution_executes(entry):
    """Render each tool with an injection payload in every parameter and execute
    it under /bin/bash; the marker file must never be created."""
    fname, ts_name, tool = entry
    param_names = list(tool.parameters.keys())
    if not param_names:
        pytest.skip("tool has no parameters")

    with tempfile.TemporaryDirectory() as workdir:
        marker = os.path.join(workdir, "PWNED")
        marker_cmd = f"touch {marker}"
        bin_dir = _make_stub_bin(workdir)
        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

        tested = 0
        for param in param_names:
            for payload in _payloads(marker_cmd):
                params = {p: "pods" for p in param_names}
                params[param] = payload
                try:
                    rendered = _render(tool, params)
                except Exception:
                    # Can't render with placeholder values (e.g. a param used via
                    # attribute access) - the static test above still covers it.
                    continue
                tested += 1
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
                assert not os.path.exists(marker), (
                    f"COMMAND INJECTION in {fname} :: {tool.name} via param "
                    f"'{param}' with payload {payload!r}.\nRendered:\n{rendered}"
                )
        if tested == 0:
            pytest.skip("no renderable parameters to fuzz")


def test_all_yaml_toolsets_load():
    """A YAML that stops loading must fail loudly, not silently shrink the
    coverage of the two guards above."""
    assert YAML_FILES, "no toolset YAML files discovered"
    assert not LOAD_FAILURES, f"toolset YAML files failed to load: {LOAD_FAILURES}"


def test_discovered_at_least_the_known_toolsets():
    """Guard the guard: make sure the loader actually found the executed
    toolsets, so a loader change can't silently reduce coverage to zero."""
    names = {ts for _f, ts, _t in ALL_TOOLS}
    for expected in ("kubernetes/core", "aks/core", "kubevela/core", "slab"):
        assert expected in names, f"{expected} not discovered - coverage gap"
