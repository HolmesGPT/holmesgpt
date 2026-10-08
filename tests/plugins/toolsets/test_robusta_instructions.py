"""Holmes connected to Robusta must describe Robusta as an AI SRE platform
(ROB-1468); without that context the model falls back to its training data and
calls it a Kubernetes observability tool."""

from unittest.mock import MagicMock

import pytest

from holmes.core.prompt import PromptComponent, build_system_prompt
from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.robusta.robusta import RobustaToolset

ABOUT_HEADER = "# About Robusta"


def _dal(enabled: bool) -> MagicMock:
    dal = MagicMock()
    dal.enabled = enabled
    return dal


def _toolset(dal) -> RobustaToolset:
    toolset = RobustaToolset(dal)
    toolset.enabled = True
    toolset.check_prerequisites()
    return toolset


def _prompt(toolsets, overrides=None) -> str:
    prompt = build_system_prompt(
        toolsets=toolsets,
        skills=None,
        system_prompt_additions=None,
        cluster_name=None,
        ask_user_enabled=False,
        prompt_component_overrides=overrides or {},
    )
    assert prompt is not None
    return prompt


def test_instructions_describe_robusta_as_ai_sre():
    instructions = _toolset(_dal(True)).llm_instructions
    assert instructions.startswith(ABOUT_HEADER)
    assert "Robusta is an AI SRE platform" in instructions
    assert "not limited to Kubernetes" in instructions
    assert "never as a Kubernetes monitoring or observability tool" in instructions
    # the pre-existing tool guidance must still be there
    assert "fetch_configuration_changes_metadata" in instructions
    assert "fetch_finding_by_id" in instructions


def test_system_prompt_includes_about_robusta_once_when_connected():
    toolset = _toolset(_dal(True))
    assert toolset.status == ToolsetStatusEnum.ENABLED
    prompt = _prompt([toolset])
    assert prompt.count(ABOUT_HEADER) == 1
    assert "Robusta is an AI SRE platform" in prompt


@pytest.mark.parametrize("dal", [None, _dal(False)], ids=["no_dal", "dal_disabled"])
def test_system_prompt_omits_about_robusta_when_not_connected(dal):
    # Open-source Holmes without a Robusta account must not claim to be Robusta's agent
    toolset = _toolset(dal)
    assert toolset.status == ToolsetStatusEnum.FAILED
    prompt = _prompt([toolset])
    assert ABOUT_HEADER not in prompt
    assert "AI SRE platform" not in prompt


def test_system_prompt_omits_about_robusta_when_toolset_not_enabled_in_config():
    toolset = RobustaToolset(_dal(True))
    assert ABOUT_HEADER not in _prompt([toolset])


def test_about_robusta_follows_toolset_instructions_component():
    toolset = _toolset(_dal(True))
    prompt = _prompt([toolset], {PromptComponent.TOOLSET_INSTRUCTIONS: False})
    assert ABOUT_HEADER not in prompt


def test_about_robusta_survives_intro_disabled():
    toolset = _toolset(_dal(True))
    prompt = _prompt([toolset], {PromptComponent.INTRO: False})
    assert "You are HolmesGPT version" not in prompt
    assert ABOUT_HEADER in prompt
