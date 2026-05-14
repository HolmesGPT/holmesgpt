"""Unit tests for usage_recorder.build_chat_recorder_state.

The helper is shared by every entry point that consumes a ChatRequest and
wraps a stream — direct /api/chat (server.py) and the worker path
(conversations_worker/worker.py). These tests cover the derivation logic
without touching either entry point so they remain stable while the
calling code evolves.

Coverage:
- is_internal derivation (explicit True/False vs internal_-prefix fallback)
- Slack-prefix auto-detection on `ask` (request_type / request_source /
  meta.slack)
- conversation_source default ('chat_history' when conversation_id set
  but unset by caller)
- Smoke: model / provider / user_id / source_ref / meta passthrough
"""

from unittest.mock import MagicMock

from holmes.core.usage_recorder import build_chat_recorder_state


def _make_request_ai(model="openai/gpt-4", is_robusta=False):
    ai = MagicMock()
    ai.llm = MagicMock()
    ai.llm.model = model
    ai.llm.is_robusta_model = is_robusta
    return ai


def _chat_request(**overrides):
    """Build a minimal ChatRequest. Imports lazily so server-import side
    effects don't hit collection-time."""
    from holmes.core.models import ChatRequest

    base = dict(ask="test question", stream=False)
    base.update(overrides)
    return ChatRequest(**base)


def _dal():
    """A stand-in dal — build_chat_recorder_state only stores it on the
    state, doesn't call it. We never fire the recorder in these tests."""
    return MagicMock()


class TestIsInternalDerivation:
    def test_explicit_true_wins(self):
        req = _chat_request(is_internal=True, request_source="freeform")
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.is_internal is True

    def test_explicit_false_wins_even_with_internal_prefix(self):
        # FE may have a "freeform" request labeled with an internal_-prefixed
        # request_source for some reason; the explicit False should still win.
        req = _chat_request(
            is_internal=False, request_source="internal_legacy_user_chat"
        )
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.is_internal is False

    def test_unset_falls_back_to_internal_prefix(self):
        # Backwards-compat: existing FE clients use the internal_ prefix
        # convention without setting is_internal explicitly.
        req = _chat_request(request_source="internal_title_generation")
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.is_internal is True

    def test_unset_with_no_prefix_yields_false(self):
        req = _chat_request(request_source="freeform")
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.is_internal is False

    def test_unset_with_no_request_source_yields_false(self):
        # No FE labeling at all → not internal.
        req = _chat_request()
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.is_internal is False


class TestRecorderStateSmoke:
    """Catch obvious wiring regressions in build_chat_recorder_state."""

    def test_carries_through_basic_fields(self):
        req = _chat_request(
            user_id="u-abc",
            conversation_id="conv-123",
            request_source="alert_investigation",
            source_ref="issue-42",
            meta={"experiment_id": "x"},
        )
        state = build_chat_recorder_state(
            req,
            _make_request_ai(model="anthropic/claude-sonnet-4-5"),
            dal=_dal(),
            is_streaming=True,
        )

        assert state.request_type == "user_chat"  # default
        assert state.request_source == "alert_investigation"
        assert state.source_ref == "issue-42"
        assert state.conversation_id == "conv-123"
        # Default for direct /api/chat: chat_history when conversation_id is set.
        assert state.conversation_source == "chat_history"
        assert state.user_id == "u-abc"
        assert state.is_streaming is True
        assert state.is_internal is False
        assert state.model == "anthropic/claude-sonnet-4-5"
        assert state.meta == {"experiment_id": "x"}

    def test_explicit_conversation_source_wins(self):
        # Worker passes conversation_source='conversations' explicitly when
        # constructing the ChatRequest; the helper must not clobber it with
        # its 'chat_history' default.
        req = _chat_request(
            conversation_id="conv-123", conversation_source="conversations"
        )
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        assert state.conversation_source == "conversations"

    def test_no_conversation_id_means_no_conversation_source(self):
        # CLI / scheduled-prompt-style requests have no conversation; the
        # discriminator must stay None so dashboards know not to join either
        # table.
        req = _chat_request()
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.conversation_id is None
        assert state.conversation_source is None


# Sample of the prefix the Robusta runner's Slack handler prepends to `ask`.
SLACK_ASK = (
    "**@user_U0AKMP2CZ97** • 2026-05-04T05:10:04Z\n\nhigh cpu in pod alert"
)


class TestSlackAutoDetect:
    def test_slack_prefix_sets_request_type_to_slack_chat(self):
        req = _chat_request(ask=SLACK_ASK)
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        assert state.request_type == "slack_chat"

    def test_slack_prefix_sets_request_source_to_slack(self):
        req = _chat_request(ask=SLACK_ASK)
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        assert state.request_source == "slack"

    def test_slack_prefix_captures_user_id_and_ts_in_meta(self):
        req = _chat_request(ask=SLACK_ASK)
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        assert state.meta.get("slack") == {
            "slack_user_id": "U0AKMP2CZ97",
            "slack_triggered_at": "2026-05-04T05:10:04Z",
        }

    def test_explicit_request_type_wins_over_slack_detection(self):
        # Even with the Slack-shaped prefix, an explicit request_type must win
        # (e.g. a future caller that overrides for some reason).
        req = _chat_request(ask=SLACK_ASK, request_type="user_chat")
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        assert state.request_type == "user_chat"
        # Slack metadata is still extracted — we don't drop the signal just
        # because the type was overridden.
        assert state.meta.get("slack", {}).get("slack_user_id") == "U0AKMP2CZ97"

    def test_explicit_request_source_wins_over_slack_default(self):
        # Same caller-wins semantic for request_source: if the runner ever
        # ships finer values like 'slack_mention' / 'slack_alert_investigation',
        # those should not be clobbered by the auto-detected default.
        req = _chat_request(ask=SLACK_ASK, request_source="slack_mention")
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        assert state.request_source == "slack_mention"
        # request_type still auto-set since it wasn't explicitly provided.
        assert state.request_type == "slack_chat"

    def test_no_slack_prefix_uses_default_request_type_and_no_source(self):
        req = _chat_request(ask="why is my-service crashing?")
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.request_type == "user_chat"
        assert state.request_source is None
        assert "slack" not in state.meta

    def test_slack_meta_merges_with_fe_meta(self):
        req = _chat_request(ask=SLACK_ASK, meta={"experiment_id": "abc"})
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=True
        )
        # Both keys preserved; backend doesn't clobber FE meta.
        assert state.meta == {
            "experiment_id": "abc",
            "slack": {
                "slack_user_id": "U0AKMP2CZ97",
                "slack_triggered_at": "2026-05-04T05:10:04Z",
            },
        }

    def test_partial_slack_prefix_does_not_match(self):
        # Just a markdown bold, no • or timestamp — must not falsely match.
        req = _chat_request(
            ask="**@user_U0AKMP2CZ97** asked: why is my pod down?"
        )
        state = build_chat_recorder_state(
            req, _make_request_ai(), dal=_dal(), is_streaming=False
        )
        assert state.request_type == "user_chat"
        assert state.request_source is None
        assert "slack" not in state.meta
