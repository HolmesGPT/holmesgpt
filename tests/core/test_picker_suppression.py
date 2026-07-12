"""Tests for withholding a frontend multiple-choice picker on a clarifying-question turn.

Bug: in a DB-backed setup chat, a PromptMultipleChoice (noop) picker is resolved
same-turn, so nothing blocks the model. When the user's follow-up is a clarifying
question rather than an option title, the model deterministically re-emits the same
picker instead of answering. Fix: withhold the picker tool for that turn so the model
must answer; the picker stays visible in the UI for the user to pick afterward.
"""

import json

from holmes.core.tool_calling_llm import (
    _picker_option_titles,
    pickers_to_suppress,
)


def _picker_call(name="PromptMultipleChoice", titles=("Workload Identity", "Service Principal"), **extra):
    return {
        "id": "tooluse_1",
        "function": {
            "name": name,
            "arguments": json.dumps(
                {
                    "question": "Which auth method?",
                    "options": [{"title": t, "description": f"desc {t}"} for t in titles],
                }
            ),
        },
        **extra,
    }


def _picker_turn(titles=("Workload Identity", "Service Principal")):
    """A resolved noop picker turn: assistant tool_call + its tool result."""
    return [
        {"role": "assistant", "content": "Here are your options.", "tool_calls": [_picker_call(titles=titles)]},
        {"role": "tool", "tool_call_id": "tooluse_1", "name": "PromptMultipleChoice", "content": "shown"},
    ]


BASE = [{"role": "system", "content": "sys"}, {"role": "user", "content": "help me set up azure"}]


class TestPickersToSuppress:
    def test_clarifying_question_suppresses_picker(self):
        messages = BASE + _picker_turn() + [
            {"role": "user", "content": "what is the difference between them?"}
        ]
        assert pickers_to_suppress(messages) == {"PromptMultipleChoice"}

    def test_exact_option_title_is_a_selection_not_suppressed(self):
        messages = BASE + _picker_turn() + [{"role": "user", "content": "Workload Identity"}]
        assert pickers_to_suppress(messages) == set()

    def test_selection_match_is_case_and_whitespace_insensitive(self):
        messages = BASE + _picker_turn() + [{"role": "user", "content": "  workload identity  "}]
        assert pickers_to_suppress(messages) == set()

    def test_no_picker_in_history_suppresses_nothing(self):
        messages = BASE + [
            {"role": "assistant", "content": "here is the answer"},
            {"role": "user", "content": "another question"},
        ]
        assert pickers_to_suppress(messages) == set()

    def test_pending_frontend_pause_picker_is_ignored(self):
        # An unresolved pause-flow call is handled elsewhere; don't touch it here.
        pending = _picker_call(pending_frontend=True)
        messages = BASE + [
            {"role": "assistant", "content": "", "tool_calls": [pending]},
            {"role": "user", "content": "what is the difference between them?"},
        ]
        assert pickers_to_suppress(messages) == set()

    def test_non_picker_tool_call_is_ignored(self):
        messages = BASE + [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "t1", "function": {"name": "kubectl_get", "arguments": json.dumps({"kind": "pods"})}}
                ],
            },
            {"role": "user", "content": "what does that mean?"},
        ]
        assert pickers_to_suppress(messages) == set()

    def test_most_recent_picker_defines_the_options(self):
        # Two pickers; the latest one's options are what matter.
        messages = (
            BASE
            + _picker_turn(titles=("A", "B"))
            + [{"role": "user", "content": "A"}]
            + [
                {"role": "assistant", "content": "next", "tool_calls": [_picker_call(titles=("Yes", "No"))]},
                {"role": "tool", "tool_call_id": "tooluse_1", "name": "PromptMultipleChoice", "content": "shown"},
                {"role": "user", "content": "why would I choose Yes?"},
            ]
        )
        assert pickers_to_suppress(messages) == {"PromptMultipleChoice"}

    def test_vision_style_user_content_is_extracted(self):
        messages = BASE + _picker_turn() + [
            {"role": "user", "content": [{"type": "text", "text": "Service Principal"}]}
        ]
        assert pickers_to_suppress(messages) == set()  # matches an option → selection

    def test_no_user_message_suppresses_nothing(self):
        messages = [{"role": "system", "content": "sys"}] + _picker_turn()
        assert pickers_to_suppress(messages) == set()


class TestPickerOptionTitles:
    def test_parses_titles_from_json_arguments(self):
        assert _picker_option_titles(_picker_call(titles=("X", "Y"))) == ["X", "Y"]

    def test_accepts_dict_arguments(self):
        tc = {"function": {"name": "p", "arguments": {"options": [{"title": "Z"}]}}}
        assert _picker_option_titles(tc) == ["Z"]

    def test_returns_none_for_non_picker_shape(self):
        assert _picker_option_titles({"function": {"name": "k", "arguments": json.dumps({"kind": "pods"})}}) is None

    def test_returns_none_for_malformed_arguments(self):
        assert _picker_option_titles({"function": {"name": "p", "arguments": "{not json"}}) is None

    def test_returns_none_for_pending_frontend(self):
        assert _picker_option_titles(_picker_call(pending_frontend=True)) is None

    def test_empty_options_returns_none(self):
        tc = {"function": {"name": "p", "arguments": json.dumps({"options": []})}}
        assert _picker_option_titles(tc) is None
