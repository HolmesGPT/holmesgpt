"""Tests for recovering answers emitted alongside a frontend tool call.

See join_frontend_tool_turn_content: models may write their final answer as
content on the same turn as a frontend tool call, which LLMResult.result drops.
"""

from tests.llm.utils.skill_suggestions import join_frontend_tool_turn_content

FRONTEND = {"SuggestSkills"}


def _assistant(content, *tool_names):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"function": {"name": name}} for name in tool_names],
    }


def test_content_beside_frontend_tool_is_recovered():
    messages = [_assistant("The pod OOMed.", "SuggestSkills")]

    output = join_frontend_tool_turn_content("Let me know if you want more.", messages, FRONTEND)

    assert "The pod OOMed." in output
    assert "Let me know if you want more." in output


def test_content_beside_backend_tool_is_ignored():
    """Narration beside an ordinary tool call is summarized later - joining it would duplicate."""
    messages = [_assistant("Checking the pod now.", "kubectl_describe")]

    output = join_frontend_tool_turn_content("The pod OOMed.", messages, FRONTEND)

    assert output == "The pod OOMed."


def test_frontend_tool_without_content_leaves_answer_unchanged():
    messages = [_assistant("", "SuggestSkills")]

    output = join_frontend_tool_turn_content("The pod OOMed.", messages, FRONTEND)

    assert output == "The pod OOMed."


def test_turn_mixing_frontend_and_backend_tools_is_ignored():
    messages = [_assistant("Partial notes.", "SuggestSkills", "kubectl_describe")]

    output = join_frontend_tool_turn_content("The pod OOMed.", messages, FRONTEND)

    assert output == "The pod OOMed."


def test_multiple_frontend_turns_keep_their_order():
    messages = [
        _assistant("First finding.", "SuggestSkills"),
        _assistant("Second finding.", "SuggestSkills"),
    ]

    output = join_frontend_tool_turn_content("Trailing remark.", messages, FRONTEND)

    assert output.index("First finding.") < output.index("Second finding.")
    assert output.index("Second finding.") < output.index("Trailing remark.")


def test_missing_messages_falls_back_to_final_answer():
    assert join_frontend_tool_turn_content("The pod OOMed.", None, FRONTEND) == "The pod OOMed."
    assert join_frontend_tool_turn_content("The pod OOMed.", [], FRONTEND) == "The pod OOMed."
    assert join_frontend_tool_turn_content("The pod OOMed.", [_assistant("x", "SuggestSkills")], None) == "The pod OOMed."
    assert join_frontend_tool_turn_content(None, None, FRONTEND) == ""
