from typing import Dict

from holmes.core.tools import (
    StaticPrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
)
from holmes.plugins.prompts import load_and_render_prompt

template = "builtin://_toolsets_instructions.jinja2"


class DummyTool(Tool):
    def __init__(self):
        super().__init__(name="dummy_tool_name", description="tool description")

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="")

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return ""


class MockToolset(Toolset):
    def __init__(self, config: dict):
        if not config.get("description"):
            config["description"] = config.get("name")
        if config.get("enabled") is None:
            config["enabled"] = True
        config["tools"] = [DummyTool()]

        super().__init__(**config)
        if self.enabled:
            self.check_prerequisites()

    def get_example_config(self):
        return {}


def test_renders_single_toolset_with_instructions():
    """Test that template properly renders toolsets with llm_instructions."""
    toolsets = [
        MockToolset({"name": "Tool1", "llm_instructions": ""}),
        MockToolset({"name": "Tool2", "llm_instructions": ""}),
        MockToolset({"name": "Tool3", "llm_instructions": "\nInstructions for Tool3"}),
        MockToolset({"name": "Tool4", "llm_instructions": ""}),
        MockToolset({"name": "Tool5", "llm_instructions": ""}),
    ]
    result = load_and_render_prompt(template, {"toolsets": toolsets})
    print(f"** result:\n{result}")
    assert "# Available Toolsets\n" in result
    assert "## Tool3\n\nInstructions for Tool3" in result


def test_renders_toolsets_with_instructions():
    """Test that template properly renders toolsets with llm_instructions."""
    toolsets = [
        MockToolset({"name": "Tool1", "llm_instructions": "\nInstructions for Tool1"}),
        MockToolset({"name": "Tool2", "llm_instructions": ""}),
        MockToolset({"name": "Tool3", "llm_instructions": "\nInstructions for Tool3"}),
        MockToolset({"name": "Tool4", "llm_instructions": ""}),
        MockToolset({"name": "Tool5", "llm_instructions": "\nInstructions for Tool5"}),
    ]
    result = load_and_render_prompt(template, {"toolsets": toolsets})
    print(f"** result:\n{result}")
    assert "# Available Toolsets\n" in result
    assert "## Tool1\n\nInstructions for Tool1" in result
    assert "## Tool3\n\nInstructions for Tool3" in result
    assert "## Tool5\n\nInstructions for Tool5" in result


def test_renders_disabled_toolsets():
    toolsets = [
        MockToolset(
            {"name": "Toolset1", "description": "this is tool 1", "enabled": False}
        ),
        MockToolset(
            {
                "name": "Toolset2",
                "description": "this is tool 2",
                "enabled": True,
                "docs_url": "https://example.com",
                "prerequisites": [
                    StaticPrerequisite(
                        enabled=False, disabled_reason="Health check failed"
                    )
                ],
            }
        ),
    ]
    result = load_and_render_prompt(template, {"toolsets": toolsets})
    expected = """
* toolset "Toolset1": this is tool 1
    *  status: disabled
* toolset "Toolset2": this is tool 2
    *  status: The toolset is enabled but misconfigured and failed to initialize.
    *  error: Health check failed
    *  setup instructions: https://example.com
""".strip()
    print(f"** result:\n{result}")
    print(f"** expected:\n{expected}")
    assert expected in result
