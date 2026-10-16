"""Tests for the flux/core YAML toolset.

Each tool is rendered the way YAMLTool renders it at runtime (sanitize -> jinja)
and the resulting command line is checked. Optional parameters arrive in three
shapes - missing, null (strict tool calls send null for unset optional params,
which renders as 'None') and empty - and all three must drop the flag.
"""

import os
import shutil
import subprocess

import pytest
from jinja2 import Template

from holmes.plugins.toolsets import load_toolsets_from_file

FLUX_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "holmes",
    "plugins",
    "toolsets",
    "flux.yaml",
)

(TOOLSET,) = load_toolsets_from_file(FLUX_YAML, strict_check=True)
TOOLS = {t.name: t for t in TOOLSET.tools}

# Flux kind -> `flux get` / `flux export` subcommand
KIND_SUBCOMMANDS = {
    "GitRepository": "source git",
    "OCIRepository": "source oci",
    "HelmRepository": "source helm",
    "HelmChart": "source chart",
    "Bucket": "source bucket",
    "Kustomization": "kustomization",
    "HelmRelease": "helmrelease",
    "ImageRepository": "image repository",
    "ImagePolicy": "image policy",
    "ImageUpdateAutomation": "image update",
    "Alert": "alert",
    "Provider": "alert-provider",
    "Receiver": "receiver",
}


def _render(tool_name, params):
    tool = TOOLS[tool_name]
    context = tool._build_context(params)
    return Template(os.path.expandvars(tool.command)).render(context)


def test_toolset_definition():
    assert TOOLSET.name == "flux/core"
    assert [p.command for p in TOOLSET.prerequisites] == ["flux version --client"]
    # Every parameter is declared in the YAML, none is inferred from the template
    assert {name: set(tool.parameters) for name, tool in TOOLS.items()} == {
        "flux_get": {"kind", "name", "namespace", "ready"},
        "flux_export": {"kind", "name", "namespace"},
        "flux_events": {"kind", "name", "namespace"},
        "flux_tree": {"name", "namespace", "compact"},
        "flux_trace": {"kind", "name", "namespace"},
    }


def test_kind_enums_match_the_subcommand_mapping():
    assert TOOLS["flux_get"].parameters["kind"].enum == ["all", *KIND_SUBCOMMANDS]
    assert TOOLS["flux_export"].parameters["kind"].enum == list(KIND_SUBCOMMANDS)
    assert TOOLS["flux_events"].parameters["kind"].enum == list(KIND_SUBCOMMANDS)


@pytest.mark.parametrize("kind,subcommand", KIND_SUBCOMMANDS.items())
def test_every_kind_maps_to_a_subcommand(kind, subcommand):
    assert _render("flux_get", {"kind": kind}) == (
        f"flux get {subcommand} --all-namespaces"
    )
    assert _render("flux_export", {"kind": kind, "name": "x"}) == (
        f"flux export {subcommand} -- x"
    )


@pytest.mark.parametrize(
    "tool_name,params,expected",
    [
        ("flux_get", {"kind": "all"}, "flux get all --all-namespaces"),
        (
            "flux_get",
            {"kind": "all", "name": None, "namespace": None, "ready": None},
            "flux get all --all-namespaces",
        ),
        (
            "flux_get",
            {"kind": "all", "name": "", "namespace": "", "ready": ""},
            "flux get all --all-namespaces",
        ),
        (
            "flux_get",
            {"kind": "all", "namespace": "apps", "ready": "false"},
            "flux get all --namespace=apps --status-selector=ready=false",
        ),
        # `flux get all` takes no name
        ("flux_get", {"kind": "all", "name": "podinfo"}, "flux get all --all-namespaces"),
        (
            "flux_get",
            {"kind": "GitRepository", "name": "podinfo", "namespace": "apps"},
            "flux get source git --namespace=apps -- podinfo",
        ),
        (
            "flux_export",
            {"kind": "HelmRelease", "name": "redis", "namespace": "cache"},
            "flux export helmrelease --namespace=cache -- redis",
        ),
        (
            "flux_export",
            {"kind": "Kustomization", "name": "apps", "namespace": None},
            "flux export kustomization -- apps",
        ),
        ("flux_events", {}, "flux events --all-namespaces"),
        (
            "flux_events",
            {"kind": None, "name": None, "namespace": None},
            "flux events --all-namespaces",
        ),
        (
            "flux_events",
            {"kind": "HelmRelease"},
            "flux events --all-namespaces --for=HelmRelease",
        ),
        (
            "flux_events",
            {"kind": "Kustomization", "name": "apps", "namespace": "apps"},
            "flux events --namespace=apps --for=Kustomization/apps",
        ),
        # --for does not work across namespaces, so flux falls back to flux-system
        (
            "flux_events",
            {"kind": "Kustomization", "name": "apps"},
            "flux events --for=Kustomization/apps",
        ),
        (
            "flux_tree",
            {"name": "flux-system", "compact": True},
            "flux tree kustomization --compact -- flux-system",
        ),
        (
            "flux_tree",
            {"name": "apps", "namespace": "apps", "compact": None},
            "flux tree kustomization --namespace=apps -- apps",
        ),
        (
            "flux_trace",
            {"kind": "Deployment", "name": "podinfo", "namespace": "apps"},
            "flux trace --namespace=apps -- Deployment podinfo",
        ),
        (
            "flux_trace",
            {"kind": "Namespace", "name": "apps"},
            "flux trace -- Namespace apps",
        ),
    ],
)
def test_rendered_command(tool_name, params, expected):
    assert _render(tool_name, params) == expected


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_values_cannot_be_read_as_flags(tmp_path):
    """A namespace or name that looks like a flag (e.g. --server, which would
    send the kubeconfig credentials to another host) must reach flux as a
    value, not as a flag."""
    stub = tmp_path / "flux"
    stub.write_text("#!/bin/bash\nprintf '%s\\n' \"$@\"\n")
    stub.chmod(0o755)
    command = _render(
        "flux_get",
        {
            "kind": "Kustomization",
            "name": "--server=https://example.invalid",
            "namespace": "--kubeconfig=/tmp/other",
        },
    )

    result = subprocess.run(
        command,
        shell=True,
        executable="/bin/bash",
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        check=True,
    )

    assert result.stdout.splitlines() == [
        "get",
        "kustomization",
        "--namespace=--kubeconfig=/tmp/other",
        "--",
        "--server=https://example.invalid",
    ]
