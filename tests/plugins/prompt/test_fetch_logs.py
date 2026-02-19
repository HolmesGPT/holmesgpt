import os

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets import load_toolsets_from_file
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
KUBERNETES_YAML_TOOLSET_PATH = os.path.join(
    THIS_DIR, "../../../holmes/plugins/toolsets/kubernetes_logs.yaml"
)


def test_no_logs_toolset():
    prompt = load_and_render_prompt("builtin://_fetch_logs.jinja2", {})
    assert "You have not been given access to tools to fetch kubernetes logs" in prompt


def test_kubernetes_yaml_toolset():
    toolsets = load_toolsets_from_file(KUBERNETES_YAML_TOOLSET_PATH, strict_check=True)
    toolsets[0].enabled = True
    toolsets[0].status = ToolsetStatusEnum.ENABLED
    prompt = load_and_render_prompt(
        "builtin://_fetch_logs.jinja2", {"toolsets": toolsets}
    )
    print(f"** PROMPT:\n{prompt}")
    assert "use both kubectl_previous_logs and kubectl_logs when reading logs" in prompt


def test_kubernetes_python_toolset():
    toolset = KubernetesLogsToolset()
    toolset.enabled = True
    toolset.status = ToolsetStatusEnum.ENABLED
    prompt = load_and_render_prompt(
        "builtin://_fetch_logs.jinja2", {"toolsets": [toolset]}
    )
    print(f"** PROMPT:\n{prompt}")
    assert "Use the tool `fetch_pod_logs` to access an application's logs" in prompt
