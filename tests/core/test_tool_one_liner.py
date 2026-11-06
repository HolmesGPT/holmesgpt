"""get_parameterized_one_liner builds a display label and must never raise.

The label is rendered through Jinja2 so that {{ param }} placeholders in a
command show their actual values. Plenty of valid shell commands are not valid
Jinja2 though, and a command that cannot be rendered still has to run.
"""

import pytest

from holmes.core.tools import YAMLTool

# All of these are ordinary commands an agent would emit, and all of them are
# rejected by the Jinja2 lexer with "unexpected '.'": Go templates delimit with
# {{ }} and start with a dot, which Jinja2 reads as the beginning of an
# expression.
GO_TEMPLATE_COMMANDS = [
    "kubectl get pods -o go-template='{{.metadata.name}}'",
    "kubectl get pods -o go-template='{{range .items}}{{.metadata.name}}{{end}}'",
    "kubectl get pod -o go-template='{{ .spec.nodeName }}'",
    "docker inspect -f '{{.State.Running}}' mycontainer",
    "docker ps --format '{{.Names}}\t{{.Status}}'",
]


@pytest.mark.parametrize("command", GO_TEMPLATE_COMMANDS)
def test_go_template_command_does_not_raise(command):
    tool = YAMLTool(name="bash", description="run a command", command=command)
    assert tool.get_parameterized_one_liner({}) == command


@pytest.mark.parametrize("command", GO_TEMPLATE_COMMANDS)
def test_go_template_user_description_does_not_raise(command):
    tool = YAMLTool(
        name="bash", description="run a command",
        command="echo hello", user_description=command,
    )
    assert tool.get_parameterized_one_liner({}) == command


def test_unparseable_script_does_not_raise():
    script = "for p in $(kubectl get po -o go-template='{{.items}}'); do echo $p; done"
    tool = YAMLTool(name="bash", description="run a script", script=script)
    assert tool.get_parameterized_one_liner({}) == script


def test_placeholders_still_render():
    """The fallback must not cost the normal case its substitution."""
    tool = YAMLTool(
        name="kubectl_logs", description="get logs",
        command="kubectl logs {{ pod_name }} -n {{ namespace }}",
    )
    one_liner = tool.get_parameterized_one_liner(
        {"pod_name": "api-7d9f", "namespace": "prod"}
    )
    assert one_liner == "kubectl logs api-7d9f -n prod"


def test_user_description_still_preferred_over_command():
    tool = YAMLTool(
        name="kubectl_logs", description="get logs",
        command="kubectl logs {{ pod_name }}",
        user_description="Fetching logs for {{ pod_name }}",
    )
    assert tool.get_parameterized_one_liner({"pod_name": "api-7d9f"}) == (
        "Fetching logs for api-7d9f"
    )
