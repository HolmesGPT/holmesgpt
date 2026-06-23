"""Unit tests for the eval framework's frontend_tools loading
(tests/llm/utils/test_case_utils.py::load_frontend_tools)."""

import pytest

from tests.llm.utils.test_case_utils import AskHolmesTestCase, load_frontend_tools


def _make_test_case(folder: str, frontend_tools) -> AskHolmesTestCase:
    return AskHolmesTestCase(
        id="0",
        folder=folder,
        expected_output=["x"],
        user_prompt="y",
        frontend_tools=frontend_tools,
    )


def test_unset_frontend_tools_defaults_to_shared_suggest_skills(tmp_path):
    # Unset mirrors production: the Robusta UI sends the SuggestSkills tool
    # with every chat request, so evals inject the shared fixture by default.
    payload = load_frontend_tools(_make_test_case(str(tmp_path), None))
    assert payload is not None
    assert [t.name for t in payload.tools] == ["SuggestSkills"]
    assert payload.additional_system_prompt


def test_empty_list_opts_out_of_default(tmp_path):
    assert load_frontend_tools(_make_test_case(str(tmp_path), [])) is None


def test_inline_frontend_tools(tmp_path):
    tc = _make_test_case(
        str(tmp_path),
        [{"name": "MyTool", "description": "d", "mode": "noop"}],
    )
    payload = load_frontend_tools(tc)
    assert [t.name for t in payload.tools] == ["MyTool"]
    assert payload.additional_system_prompt is None


def test_frontend_tools_from_file_with_system_prompt(tmp_path):
    (tmp_path / "tools.yaml").write_text(
        "additional_system_prompt: extra instructions\n"
        "frontend_tools:\n"
        "  - name: MyTool\n"
        "    description: d\n"
        "    mode: noop\n"
        "    noop_response: ok\n"
    )
    payload = load_frontend_tools(_make_test_case(str(tmp_path), "tools.yaml"))
    assert [t.name for t in payload.tools] == ["MyTool"]
    assert payload.tools[0].noop_response == "ok"
    assert payload.additional_system_prompt == "extra instructions"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_frontend_tools(_make_test_case(str(tmp_path), "nope.yaml"))


def test_pause_mode_rejected(tmp_path):
    tc = _make_test_case(
        str(tmp_path),
        [{"name": "PauseTool", "description": "d", "mode": "pause"}],
    )
    with pytest.raises(ValueError, match="noop"):
        load_frontend_tools(tc)


def test_shared_skill_suggestion_fixture_loads():
    """The shared fixture used by the 271-274 skill-suggestion evals must
    parse and carry both the tool and the system prompt snippet."""
    tc = _make_test_case(
        "tests/llm/fixtures/test_ask_holmes/271_skill_suggestion_elasticsearch",
        "../../shared/skill_suggestion_tool.yaml",
    )
    payload = load_frontend_tools(tc)
    assert [t.name for t in payload.tools] == ["SuggestSkills"]
    assert "SuggestSkills" in payload.additional_system_prompt
