from typing import Any, Optional
from unittest.mock import Mock

from holmes.core.feedback import (
    Feedback,
    FeedbackCallback,
    FeedbackLLM,
    FeedbackLLMResponse,
    FeedbackMetadata,
    UserFeedback,
)
from holmes.core.llm import LLM, TokenCountMetadata


class MockLLM(LLM):
    """Mock LLM class for testing."""

    def __init__(self, model: str = "gpt-3.5-turbo", context_size: int = 4096):
        self.model = model
        self._context_size = context_size

    def get_context_window_size(self) -> int:
        return self._context_size

    def get_maximum_output_token(self) -> int:
        return 1024

    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> TokenCountMetadata:
        return TokenCountMetadata(
            total_tokens=100,
            system_tokens=0,
            tools_to_call_tokens=0,
            tools_tokens=0,
            user_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )

    def completion(self, *args, **kwargs):
        return Mock()


class TestFeedbackLLM:
    """Test suite for FeedbackLLM class."""

    def test_init(self):
        """Test FeedbackLLM initialization."""
        feedback_llm = FeedbackLLM("gpt-4", 8192)

        assert feedback_llm.model == "gpt-4"
        assert feedback_llm.max_context_size == 8192

    def test_update_from_llm(self):
        """Test updating FeedbackLLM from LLM instance."""
        feedback_llm = FeedbackLLM("old-model", 1000)
        mock_llm = MockLLM("new-model", 4096)

        feedback_llm.update_from_llm(mock_llm)

        assert feedback_llm.model == "new-model"
        assert feedback_llm.max_context_size == 4096

    def test_to_dict(self):
        """Test converting FeedbackLLM to dictionary."""
        feedback_llm = FeedbackLLM("gpt-4", 8192)
        result = feedback_llm.to_dict()

        expected = {"model": "gpt-4", "max_context_size": 8192}
        assert result == expected


class TestFeedbackLLMResponse:
    """Test suite for FeedbackLLMResponse class."""

    def test_init(self):
        """Test FeedbackLLMResponse initialization."""
        response = FeedbackLLMResponse("What is the weather?", "The weather is sunny.")

        assert response.user_ask == "What is the weather?"
        assert response.response == "The weather is sunny."

    def test_to_dict(self):
        """Test converting FeedbackLLMResponse to dictionary."""
        response = FeedbackLLMResponse("Hello", "Hi there!")
        result = response.to_dict()

        expected = {"user_ask": "Hello", "response": "Hi there!"}
        assert result == expected

    def test_empty_strings(self):
        """Test FeedbackLLMResponse with empty strings."""
        response = FeedbackLLMResponse("", "")

        assert response.user_ask == ""
        assert response.response == ""
        assert response.to_dict() == {"user_ask": "", "response": ""}


class TestFeedbackMetadata:
    """Test suite for FeedbackMetadata class."""

    def test_init_default(self):
        """Test FeedbackMetadata initialization with defaults."""
        metadata = FeedbackMetadata()

        assert metadata.llm_responses == []
        assert isinstance(metadata.llm, FeedbackLLM)
        assert metadata.llm.model == ""
        assert metadata.llm.max_context_size == 0

    def test_add_llm_response(self):
        """Test adding LLM response to metadata."""
        metadata = FeedbackMetadata()

        metadata.add_llm_response("How are you?", "I'm doing well!")

        assert len(metadata.llm_responses) == 1
        assert metadata.llm_responses[0].user_ask == "How are you?"
        assert metadata.llm_responses[0].response == "I'm doing well!"

    def test_add_multiple_llm_responses(self):
        """Test adding multiple LLM responses."""
        metadata = FeedbackMetadata()

        metadata.add_llm_response("First question", "First answer")
        metadata.add_llm_response("Second question", "Second answer")

        assert len(metadata.llm_responses) == 2
        assert metadata.llm_responses[0].user_ask == "First question"
        assert metadata.llm_responses[1].user_ask == "Second question"

    def test_update_llm(self):
        """Test updating LLM information in metadata."""
        metadata = FeedbackMetadata()
        mock_llm = MockLLM("claude-3", 12000)

        metadata.update_llm(mock_llm)

        assert metadata.llm.model == "claude-3"
        assert metadata.llm.max_context_size == 12000

    def test_to_dict_empty(self):
        """Test converting empty FeedbackMetadata to dictionary."""
        metadata = FeedbackMetadata()
        result = metadata.to_dict()

        expected = {"llm_responses": [], "llm": {"model": "", "max_context_size": 0}}
        assert result == expected

    def test_to_dict_with_data(self):
        """Test converting FeedbackMetadata with data to dictionary."""
        metadata = FeedbackMetadata()
        metadata.add_llm_response("Question", "Answer")
        mock_llm = MockLLM("gpt-4", 8192)
        metadata.update_llm(mock_llm)

        result = metadata.to_dict()

        expected = {
            "llm_responses": [{"user_ask": "Question", "response": "Answer"}],
            "llm": {"model": "gpt-4", "max_context_size": 8192},
        }
        assert result == expected


class TestUserFeedback:
    """Test suite for UserFeedback class."""

    def test_init_positive(self):
        """Test UserFeedback initialization with positive feedback."""
        feedback = UserFeedback(True, "Great response!")

        assert feedback.is_positive is True
        assert feedback.comment == "Great response!"

    def test_init_negative(self):
        """Test UserFeedback initialization with negative feedback."""
        feedback = UserFeedback(False, "Could be better")

        assert feedback.is_positive is False
        assert feedback.comment == "Could be better"

    def test_init_no_comment(self):
        """Test UserFeedback initialization without comment."""
        feedback = UserFeedback(True, None)

        assert feedback.is_positive is True
        assert feedback.comment is None

    def test_rating_text_positive(self):
        """Test rating_text property for positive feedback."""
        feedback = UserFeedback(True, "Good")
        assert feedback.rating_text == "useful"

    def test_rating_text_negative(self):
        """Test rating_text property for negative feedback."""
        feedback = UserFeedback(False, "Bad")
        assert feedback.rating_text == "not useful"

    def test_rating_emoji_positive(self):
        """Test rating_emoji property for positive feedback."""
        feedback = UserFeedback(True, "Good")
        assert feedback.rating_emoji == "👍"

    def test_rating_emoji_negative(self):
        """Test rating_emoji property for negative feedback."""
        feedback = UserFeedback(False, "Bad")
        assert feedback.rating_emoji == "👎"

    def test_str_with_comment(self):
        """Test string representation with comment."""
        feedback = UserFeedback(True, "Very helpful!")
        expected = "Rating: useful. Comment: Very helpful!"
        assert str(feedback) == expected

    def test_str_without_comment(self):
        """Test string representation without comment."""
        feedback = UserFeedback(False, None)
        expected = "Rating: not useful. No additional comment."
        assert str(feedback) == expected

    def test_to_dict_positive_with_comment(self):
        """Test converting positive feedback with comment to dictionary."""
        feedback = UserFeedback(True, "Excellent work!")
        result = feedback.to_dict()

        expected = {
            "is_positive": True,
            "comment": "Excellent work!",
        }
        assert result == expected

    def test_to_dict_negative_without_comment(self):
        """Test converting negative feedback without comment to dictionary."""
        feedback = UserFeedback(False, None)
        result = feedback.to_dict()

        expected = {
            "is_positive": False,
            "comment": None,
        }
        assert result == expected


class TestFeedback:
    """Test suite for Feedback class."""

    def test_init(self):
        """Test Feedback initialization."""
        feedback = Feedback()

        assert isinstance(feedback.metadata, FeedbackMetadata)
        assert feedback.user_feedback is None

    def test_set_user_feedback(self):
        """Test setting user feedback."""
        feedback = Feedback()
        user_feedback = UserFeedback(True, "Great!")

        feedback.set_user_feedback(user_feedback)

        assert feedback.user_feedback == user_feedback
        assert feedback.user_feedback.is_positive is True
        assert feedback.user_feedback.comment == "Great!"

    def test_to_dict_without_user_feedback(self):
        """Test converting Feedback without user feedback to dictionary."""
        feedback = Feedback()
        result = feedback.to_dict()

        expected = {
            "metadata": {
                "llm_responses": [],
                "llm": {"model": "", "max_context_size": 0},
            },
            "user_feedback": None,
        }
        assert result == expected

    def test_to_dict_with_user_feedback(self):
        """Test converting Feedback with user feedback to dictionary."""
        feedback = Feedback()
        user_feedback = UserFeedback(True, "Helpful response")
        feedback.set_user_feedback(user_feedback)

        # Add some metadata
        feedback.metadata.add_llm_response("Question", "Answer")
        mock_llm = MockLLM("gpt-3.5-turbo", 4096)
        feedback.metadata.update_llm(mock_llm)

        result = feedback.to_dict()

        expected = {
            "metadata": {
                "llm_responses": [{"user_ask": "Question", "response": "Answer"}],
                "llm": {"model": "gpt-3.5-turbo", "max_context_size": 4096},
            },
            "user_feedback": {
                "is_positive": True,
                "comment": "Helpful response",
            },
        }
        assert result == expected

    def test_complete_workflow(self):
        """Test complete feedback workflow."""
        feedback = Feedback()

        # Update LLM information
        mock_llm = MockLLM("claude-3.5-sonnet", 16000)
        feedback.metadata.update_llm(mock_llm)

        # Add conversation history
        feedback.metadata.add_llm_response(
            "What is Python?", "Python is a programming language."
        )
        feedback.metadata.add_llm_response(
            "How do I install it?", "You can install Python from python.org"
        )

        # Set user feedback
        user_feedback = UserFeedback(True, "Very informative answers!")
        feedback.set_user_feedback(user_feedback)

        # Verify complete structure
        result = feedback.to_dict()
        assert len(result["metadata"]["llm_responses"]) == 2
        assert result["metadata"]["llm"]["model"] == "claude-3.5-sonnet"
        assert result["user_feedback"]["is_positive"] is True
        assert result["user_feedback"]["comment"] == "Very informative answers!"


class TestFeedbackCallback:
    """Test suite for FeedbackCallback type."""

    def test_callback_signature(self):
        """Test that FeedbackCallback can be used as a type hint."""

        def sample_callback(feedback: Feedback) -> None:
            """Sample callback function."""
            pass

        # This should type check correctly
        callback: FeedbackCallback = sample_callback

        # Test that it can be called with a Feedback object
        feedback = Feedback()
        callback(feedback)

    def test_callback_with_mock(self):
        """Test callback functionality with mock."""
        mock_callback = Mock()

        feedback = Feedback()
        user_feedback = UserFeedback(True, "Test feedback")
        feedback.set_user_feedback(user_feedback)

        # Call the mock callback
        mock_callback(feedback)

        # Verify it was called with the feedback
        mock_callback.assert_called_once_with(feedback)
