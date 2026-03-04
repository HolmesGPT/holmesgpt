from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from litellm.types.utils import ModelResponse

from holmes.common import holmes_context
from holmes.core.llm import DefaultLLM
from server import app


class TestHolmesContext:
    """Test the holmes_context module for feature_id get/set."""

    def test_set_and_get_feature_id(self):
        holmes_context.set_feature_id("my-feature-123")
        assert holmes_context.get_feature_id() == "my-feature-123"

    def test_get_feature_id_returns_default_when_not_set(self):
        """When no feature_id has been set, the default fallback should be returned."""
        # Reset context to empty
        holmes_context._holmes_context.set({})
        assert holmes_context.get_feature_id() == "holmes_unknown"


class TestDefaultLLMFeatureIDHeader:
    """Test that DefaultLLM.completion() injects X-Feature-ID header for Robusta models."""

    def _create_llm(
        self, is_robusta_model: bool, args: dict | None = None
    ) -> DefaultLLM:
        with patch.object(DefaultLLM, "check_llm"):
            return DefaultLLM(
                model="test-model",
                api_key="test-key",
                is_robusta_model=is_robusta_model,
                args=args or {},
            )

    @patch("holmes.core.llm.litellm")
    def test_robusta_model_adds_feature_id_header(self, mock_litellm):
        """When is_robusta_model=True and feature_id is set, X-Feature-ID should be in extra_headers."""
        holmes_context.set_feature_id("feat-abc")
        mock_litellm.completion.return_value = ModelResponse()
        mock_litellm.modify_params = False

        llm = self._create_llm(is_robusta_model=True)
        llm.completion(messages=[{"role": "user", "content": "test"}])

        _, call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs["extra_headers"]["X-Feature-ID"] == "feat-abc"

    @patch("holmes.core.llm.litellm")
    def test_robusta_model_preserves_existing_extra_headers(self, mock_litellm):
        """Existing extra_headers should be preserved when feature_id is injected."""
        holmes_context.set_feature_id("feat-xyz")
        mock_litellm.completion.return_value = ModelResponse()
        mock_litellm.modify_params = False

        llm = self._create_llm(
            is_robusta_model=True,
            args={"extra_headers": {"X-Custom": "keep-me"}},
        )
        llm.completion(messages=[{"role": "user", "content": "test"}])

        _, call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs["extra_headers"]["X-Feature-ID"] == "feat-xyz"
        assert call_kwargs["extra_headers"]["X-Custom"] == "keep-me"

    @patch("holmes.core.llm.litellm")
    def test_robusta_model_does_not_leak_feature_id_into_self_args(self, mock_litellm):
        """Injecting feature_id header should not leak X-Feature-ID into llm.args."""
        holmes_context.set_feature_id("feat-1")
        mock_litellm.completion.return_value = ModelResponse()
        mock_litellm.modify_params = False

        llm = self._create_llm(is_robusta_model=True)

        llm.completion(messages=[{"role": "user", "content": "call 1"}])

        # extra_headers with X-Feature-ID should NOT be in self.args
        assert "X-Feature-ID" not in llm.args.get("extra_headers", {})

    @patch("holmes.core.llm.litellm")
    def test_non_robusta_model_does_not_add_feature_id_header(self, mock_litellm):
        """When is_robusta_model=False, no X-Feature-ID header should be added."""
        holmes_context.set_feature_id("feat-should-not-appear")
        mock_litellm.completion.return_value = ModelResponse()
        mock_litellm.modify_params = False

        llm = self._create_llm(is_robusta_model=False)
        llm.completion(messages=[{"role": "user", "content": "test"}])

        _, call_kwargs = mock_litellm.completion.call_args
        extra_headers = call_kwargs.get("extra_headers", {})
        assert "X-Feature-ID" not in extra_headers

    @patch("holmes.core.llm.litellm")
    def test_robusta_model_uses_default_feature_id_when_not_set(self, mock_litellm):
        """When no feature_id is explicitly set, the default fallback should be used."""
        holmes_context._holmes_context.set({})
        mock_litellm.completion.return_value = ModelResponse()
        mock_litellm.modify_params = False

        llm = self._create_llm(is_robusta_model=True)
        llm.completion(messages=[{"role": "user", "content": "test"}])

        _, call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs["extra_headers"]["X-Feature-ID"] == "holmes_unknown"


class TestServerMiddlewareFeatureID:
    """Test that the server middleware extracts X-Feature-ID and sets it in holmes_context."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_middleware_sets_feature_id_from_header(
        self,
        mock_get_global_instructions,
        mock_create_toolcalling_llm,
        client,
    ):
        """When X-Feature-ID is in request headers, it should be set in holmes_context."""
        captured_feature_ids = []

        mock_ai = MagicMock()

        def capture_context(messages, **kwargs):
            captured_feature_ids.append(holmes_context.get_feature_id())
            return MagicMock(
                result="ok",
                tool_calls=[],
                messages=messages,
                metadata={},
            )

        mock_ai.messages_call.side_effect = capture_context
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "conversation_history": [], "model": "gpt-4.1"}
        client.post(
            "/api/chat",
            json=payload,
            headers={"X-Feature-ID": "server-feat-42"},
        )

        assert len(captured_feature_ids) == 1
        assert captured_feature_ids[0] == "server-feat-42"
