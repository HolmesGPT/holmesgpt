"""Unit tests for the Karpenter toolsets.

These tests load ``holmes/plugins/toolsets/karpenter.yaml`` directly and verify
the declared toolsets (``karpenter/core`` and ``karpenter/aws``), their
prerequisites, the tool inventory and that parameterized commands render
correctly. They do not execute ``kubectl`` — they only exercise the YAML
definition and Jinja rendering.
"""

from pathlib import Path

import pytest

from holmes.plugins.toolsets import load_toolsets_from_file


KARPENTER_YAML = str(
    Path(__file__).resolve().parents[3]
    / "holmes"
    / "plugins"
    / "toolsets"
    / "karpenter.yaml"
)


EXPECTED_CORE_TOOLS = {
    "karpenter_nodepool_list",
    "karpenter_nodepool_get",
    "karpenter_nodeclaim_list",
    "karpenter_nodeclaim_get",
    "karpenter_nodeclaim_describe",
    "karpenter_disruption_events",
    "karpenter_controller_logs",
    "karpenter_pending_pods",
}

EXPECTED_AWS_TOOLS = {
    "karpenter_ec2nodeclass_list",
    "karpenter_ec2nodeclass_get",
}


@pytest.fixture(scope="module")
def karpenter_toolsets():
    """Load both toolsets declared in karpenter.yaml, keyed by name."""
    toolsets = load_toolsets_from_file(KARPENTER_YAML, strict_check=True)
    assert len(toolsets) == 2, (
        "karpenter.yaml should declare karpenter/core and karpenter/aws"
    )
    return {ts.name: ts for ts in toolsets}


@pytest.fixture(scope="module")
def karpenter_core(karpenter_toolsets):
    """Return the karpenter/core toolset."""
    return karpenter_toolsets["karpenter/core"]


@pytest.fixture(scope="module")
def karpenter_aws(karpenter_toolsets):
    """Return the karpenter/aws toolset."""
    return karpenter_toolsets["karpenter/aws"]


def test_karpenter_core_metadata(karpenter_core):
    """karpenter/core must declare description, docs URL, icon and LLM instructions."""
    assert karpenter_core.name == "karpenter/core"
    assert karpenter_core.description
    assert karpenter_core.docs_url
    assert karpenter_core.icon_url
    assert karpenter_core.llm_instructions


def test_karpenter_aws_metadata(karpenter_aws):
    """karpenter/aws must declare description, docs URL, icon and LLM instructions."""
    assert karpenter_aws.name == "karpenter/aws"
    assert karpenter_aws.description
    assert karpenter_aws.docs_url
    assert karpenter_aws.icon_url
    assert karpenter_aws.llm_instructions


def test_karpenter_core_has_all_expected_tools(karpenter_core):
    """The karpenter/core tool inventory must match EXPECTED_CORE_TOOLS exactly."""
    tool_names = {tool.name for tool in karpenter_core.tools}
    assert tool_names == EXPECTED_CORE_TOOLS


def test_karpenter_aws_has_all_expected_tools(karpenter_aws):
    """The karpenter/aws tool inventory must match EXPECTED_AWS_TOOLS exactly."""
    tool_names = {tool.name for tool in karpenter_aws.tools}
    assert tool_names == EXPECTED_AWS_TOOLS


def test_karpenter_core_prerequisites(karpenter_core):
    """karpenter/core must gate on kubectl and the NodePool CRD."""
    prereq_commands = [
        p.command
        for p in karpenter_core.prerequisites
        if getattr(p, "command", None)
    ]
    assert any("kubectl version" in c for c in prereq_commands), (
        "Toolset must verify kubectl is available"
    )
    assert any("nodepools.karpenter.sh" in c for c in prereq_commands), (
        "Toolset must verify Karpenter core CRDs are installed"
    )


def test_karpenter_aws_prerequisites(karpenter_aws):
    """karpenter/aws must additionally gate on the EC2NodeClass CRD so it
    stays disabled on AKS/GKE clusters that lack the AWS provider."""
    prereq_commands = [
        p.command
        for p in karpenter_aws.prerequisites
        if getattr(p, "command", None)
    ]
    assert any(
        "ec2nodeclasses.karpenter.k8s.aws" in c for c in prereq_commands
    ), "AWS toolset must verify the EC2NodeClass CRD is installed"


def test_karpenter_tool_parameters_are_inferred(karpenter_core, karpenter_aws):
    """Templated resource-name tools must auto-infer a `name` parameter, and
    karpenter_controller_logs must expose tunable `ns` and `lines`. The
    namespace parameter is named `ns` (not `namespace`) because `namespace` is
    a reserved identifier in Jinja2 and would shadow the built-in Namespace
    class, causing the `default` filter to silently fall through."""
    core_tools_by_name = {tool.name: tool for tool in karpenter_core.tools}
    aws_tools_by_name = {tool.name: tool for tool in karpenter_aws.tools}

    for tool_name in (
        "karpenter_nodepool_get",
        "karpenter_nodeclaim_get",
        "karpenter_nodeclaim_describe",
    ):
        assert "name" in core_tools_by_name[tool_name].parameters, (
            f"{tool_name} must expose a `name` parameter"
        )

    assert "name" in aws_tools_by_name["karpenter_ec2nodeclass_get"].parameters

    controller_logs_params = core_tools_by_name[
        "karpenter_controller_logs"
    ].parameters
    assert "lines" in controller_logs_params
    assert "ns" in controller_logs_params, (
        "controller_logs must accept an `ns` override so non-default "
        "Karpenter installations (e.g. kube-system on EKS) are reachable"
    )


def test_karpenter_commands_render_with_params(karpenter_core):
    """Sanity-check Jinja rendering for the two most-parameterized tools."""
    tools_by_name = {tool.name: tool for tool in karpenter_core.tools}

    nodeclaim_describe = tools_by_name["karpenter_nodeclaim_describe"]
    rendered = nodeclaim_describe.get_parameterized_one_liner(
        {"name": "stuck-claim-1"}
    )
    assert "stuck-claim-1" in rendered
    assert "describe nodeclaim" in rendered

    controller_logs = tools_by_name["karpenter_controller_logs"]
    rendered_logs = controller_logs.get_parameterized_one_liner({"lines": 50})
    assert "--tail=50" in rendered_logs
    assert "app.kubernetes.io/name=karpenter" in rendered_logs
    # Defaults: ns="karpenter" (upstream Helm chart), lines=500
    assert "-n karpenter" in rendered_logs

    default_logs = controller_logs.get_parameterized_one_liner({})
    assert "--tail=500" in default_logs
    assert "-n karpenter" in default_logs

    # Explicit namespace override (e.g. EKS add-on installs into kube-system).
    # We use `ns` rather than `namespace` because `namespace` is a reserved
    # name in Jinja2 (the built-in Namespace class), which would prevent the
    # `default` filter from firing.
    kube_system_logs = controller_logs.get_parameterized_one_liner(
        {"ns": "kube-system"}
    )
    assert "-n kube-system" in kube_system_logs

    # karpenter_disruption_events must bound output via `tail -n`, with a
    # default of 100 and a caller override applied when supplied.
    disruption_events = tools_by_name["karpenter_disruption_events"]
    default_events = disruption_events.get_parameterized_one_liner({})
    assert "tail -n 100" in default_events
    assert "--sort-by=.lastTimestamp" in default_events

    bounded_events = disruption_events.get_parameterized_one_liner(
        {"lines": 25}
    )
    assert "tail -n 25" in bounded_events


def test_karpenter_ec2nodeclass_get_renders_with_name(karpenter_aws):
    """karpenter_ec2nodeclass_get must interpolate the `name` parameter into
    the kubectl command and request -o yaml."""
    tools_by_name = {tool.name: tool for tool in karpenter_aws.tools}
    ec2nodeclass_get = tools_by_name["karpenter_ec2nodeclass_get"]
    rendered = ec2nodeclass_get.get_parameterized_one_liner({"name": "default"})
    assert "default" in rendered
    assert "-o yaml" in rendered
    assert "get ec2nodeclass.karpenter.k8s.aws" in rendered


def test_karpenter_nodeclaim_get_renders_yaml(karpenter_core):
    """karpenter_nodeclaim_get must request -o yaml — the raw-spec counterpart
    to karpenter_nodeclaim_describe."""
    tools_by_name = {tool.name: tool for tool in karpenter_core.tools}
    nodeclaim_get = tools_by_name["karpenter_nodeclaim_get"]
    rendered = nodeclaim_get.get_parameterized_one_liner({"name": "nc-abc"})
    assert "nc-abc" in rendered
    assert "-o yaml" in rendered
    assert "get nodeclaim.karpenter.sh" in rendered


def test_karpenter_controller_logs_has_summarize_transformer(karpenter_core):
    """Controller logs are noisy (reconcile loops); ensure llm_summarize is
    wired in to protect the context window on large tails."""
    tools_by_name = {tool.name: tool for tool in karpenter_core.tools}
    controller_logs = tools_by_name["karpenter_controller_logs"]

    transformers = getattr(controller_logs, "transformers", None) or []
    transformer_names = [getattr(t, "name", None) for t in transformers]
    assert "llm_summarize" in transformer_names, (
        "karpenter_controller_logs must declare an llm_summarize transformer"
    )
