from holmes.core.tools import YAMLTool


def test_one_liner_falls_back_to_raw_command_on_template_syntax_error():
    command = "kubectl get pods -o go-template='{{.metadata.name}}'"
    tool = YAMLTool(name="list_pods", description="List pods", command=command)
    assert tool.get_parameterized_one_liner({}) == command


def test_one_liner_renders_params():
    tool = YAMLTool(
        name="get_pod", description="Get pod", command="kubectl get pod {{ pod_name }}"
    )
    assert tool.get_parameterized_one_liner({"pod_name": "web"}) == "kubectl get pod web"
