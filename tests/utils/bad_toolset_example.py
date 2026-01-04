from typing import Dict, List

from pydantic import Field

from holmes.core.tools import Tool, Toolset


class BadTool(Tool):
    """This is a test tool to check failures and test functionality.

    BadTool intentionally lacks Field(exclude=True) on the toolset field to test
    circular reference detection in toolset serialization.
    """

    toolset: "BadToolset"

    def _invoke(self, params: dict, context):
        pass

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"Bad tool {self.name}"

    @classmethod
    def create(cls, tool, toolset):
        return cls(
            name=tool.name,
            description=tool.description or "",
            parameters={},
            toolset=toolset,
        )


class BadToolset(Toolset):
    """This is a test toolset to check failures and test functionality.

    BadToolset uses BadTool which lacks Field(exclude=True) on the toolset field,
    causing circular reference errors during serialization. This is used to verify
    that tests properly catch circular reference issues.
    """

    tools: List[BadTool] = Field(default_factory=list)  # type: ignore

    def get_example_config(self) -> Dict:
        return {}


BadTool.model_rebuild()
BadToolset.model_rebuild()
