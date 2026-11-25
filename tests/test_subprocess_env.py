import os
import sys
import unittest
from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import (
    ToolInvokeContext,
    ToolsetCommandPrerequisite,
    ToolsetStatusEnum,
    YAMLTool,
    YAMLToolset,
)
from holmes.plugins.toolsets.bash.common.bash import (
    execute_argv_command,
    execute_bash_command,
)
from holmes.utils.subprocess_env import external_program_env


class TestExternalProgramEnv(unittest.TestCase):
    def test_unfrozen_preserves_inheritance(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(external_program_env())

    def test_other_platform_preserves_inheritance(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "platform", "darwin"),
        ):
            self.assertIsNone(external_program_env())

    def test_frozen_linux_restores_original_path_without_mutating_parent(self):
        environment = {
            "LD_LIBRARY_PATH": "/bundle",
            "LD_LIBRARY_PATH_ORIG": "/system",
            "HOME": "/home/test",
        }
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "platform", "linux"),
            patch.dict(os.environ, environment, clear=True),
        ):
            result = external_program_env()
            self.assertEqual(result["LD_LIBRARY_PATH"], "/system")
            self.assertEqual(result["HOME"], "/home/test")
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/bundle")

    def test_frozen_linux_removes_path_without_original(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "platform", "linux"),
            patch.dict(os.environ, {"LD_LIBRARY_PATH": "/bundle"}, clear=True),
        ):
            self.assertNotIn("LD_LIBRARY_PATH", external_program_env())

    def test_frozen_linux_preserves_empty_original(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "platform", "linux"),
            patch.dict(
                os.environ,
                {"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": ""},
                clear=True,
            ),
        ):
            self.assertEqual(external_program_env()["LD_LIBRARY_PATH"], "")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux frozen build regression")
@pytest.mark.parametrize(
    "execution_path", ["bash", "argv", "yaml_command", "yaml_script", "prerequisite"]
)
def test_system_tools_receive_original_library_path(monkeypatch, execution_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/synthetic-bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/synthetic-original")
    command = "/usr/bin/printenv LD_LIBRARY_PATH"

    if execution_path == "bash":
        result = execute_bash_command(command, timeout=5)
        assert result.return_code == 0
        assert result.stdout == "/synthetic-original"
    elif execution_path == "argv":
        result = execute_argv_command(
            ["/usr/bin/printenv", "LD_LIBRARY_PATH"], timeout=5
        )
        assert result.return_code == 0
        assert result.stdout == "/synthetic-original"
    elif execution_path.startswith("yaml_"):
        field = "script" if execution_path == "yaml_script" else "command"
        value = "#!/bin/bash\n" + command if field == "script" else command
        tool = YAMLTool(
            name="check_env", description="Check child environment", **{field: value}
        )
        context = ToolInvokeContext.model_construct(
            llm=Mock(), max_token_count=1000, tool_call_id="test", tool_name="check_env"
        )
        result = tool._invoke({}, context)
        assert result.return_code == 0
        assert result.data == "/synthetic-original"
    else:
        toolset = YAMLToolset(
            name="check_env",
            description="Check child environment",
            tools=[],
            prerequisites=[
                ToolsetCommandPrerequisite(
                    command=command, expected_output="/synthetic-original"
                )
            ],
        )
        toolset.check_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED

    assert os.environ["LD_LIBRARY_PATH"] == "/synthetic-bundle"
