"""
Integration test for the flyio/core built-in toolset.

HolmesGPT's built-in toolsets are overwhelmingly Kubernetes/cloud-native
specific despite the "works with any infrastructure" claim — there was no
adapter for a PaaS like Fly.io. This wraps `flyctl` the same way the
existing docker.yaml wraps the Docker CLI.

See https://github.com/tsushanth/holmesgpt-toolset-flyio for real
production proof runs (two apps, two languages) validating this toolset's
diagnostic output, not just that it loads.
"""

from holmes.plugins.toolsets import load_builtin_toolsets


def _find_flyio_toolset():
    toolsets = load_builtin_toolsets()
    matches = [t for t in toolsets if t.name == "flyio/core"]
    assert (
        len(matches) == 1
    ), "flyio/core should be loaded exactly once as a built-in toolset"
    return matches[0]


def test_flyio_toolset_loads():
    toolset = _find_flyio_toolset()
    assert toolset.description
    assert "cli" in toolset.tags


def test_flyio_toolset_has_expected_tools():
    toolset = _find_flyio_toolset()
    tool_names = {tool.name for tool in toolset.tools}
    assert tool_names == {
        "fly_status",
        "fly_logs_recent",
        "fly_machine_list",
        "fly_machine_status",
        "fly_checks_list",
        "fly_releases",
        "fly_scale_show",
        "fly_secrets_list",
    }


def test_flyio_toolset_commands_use_app_placeholder():
    toolset = _find_flyio_toolset()
    for tool in toolset.tools:
        # every tool scopes to one Fly app, and none write/mutate state --
        # this is a read-only investigation toolset
        assert "{{ app }}" in tool.command
        for destructive in ("destroy", "delete", "secrets set", "scale set", "deploy"):
            assert destructive not in tool.command


def test_flyio_toolset_prerequisite_is_fly_version():
    toolset = _find_flyio_toolset()
    assert len(toolset.prerequisites) == 1
    assert toolset.prerequisites[0].command == "fly version"


def test_flyio_logs_recent_output_is_bounded():
    """fly logs --no-tail has no built-in line limit -- a noisy incident
    could return unbounded output. Pin that the command caps it, per
    CodeRabbit review feedback on this PR."""
    toolset = _find_flyio_toolset()
    tool = next(t for t in toolset.tools if t.name == "fly_logs_recent")
    assert "tail -n" in tool.command


def test_flyio_secrets_list_never_exposes_values():
    """fly_secrets_list must only ever be able to list secret NAMES -- `fly
    secrets list` never prints values, unlike e.g. `fly secrets unset` or a
    hypothetical `--reveal` flag. Pin the exact command so a future edit
    can't silently swap in something that leaks values."""
    toolset = _find_flyio_toolset()
    tool = next(t for t in toolset.tools if t.name == "fly_secrets_list")
    assert tool.command == "fly secrets list -a {{ app }}"
