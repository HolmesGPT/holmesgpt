import os
from abc import ABC, abstractmethod
from typing import Callable, Optional

from .llm import LLM

DEFAULT_PRIVACY_NOTICE_BANNER = "Your feedback will be used to improve Holmesgpt's performance. Please avoid sharing sensitive personal information. By continuing, you consent to this data usage."
PRIVACY_NOTICE_BANNER = os.environ.get(
    "PRIVACY_NOTICE_BANNER", DEFAULT_PRIVACY_NOTICE_BANNER
)


class FeedbackInfoBase(ABC):
    """Abstract base class for all feedback-related classes that must implement to_dict()."""

    @abstractmethod
    def to_dict(self) -> dict:
        """Convert to dictionary representation. Must be implemented by all subclasses."""
        pass


class FeedbackLLM(FeedbackInfoBase):
    """Class to represent a LLM in the feedback."""

    def __init__(self, model: str, max_context_size: int):
        self.model = model
        self.max_context_size = max_context_size

    def update_from_llm(self, llm: LLM):
        self.model = llm.model
        self.max_context_size = llm.get_context_window_size()

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return self.__dict__


# TODO: extend the FeedbackLLMResponse to include each tool call results details used for evaluate the overall response.
# Currenlty tool call details in plan:
# - toolcall parameter and success/failure, toolcall truncation size
# - Holmes plan (todo list)
# - Holmes intermediate output
class FeedbackLLMResponse(FeedbackInfoBase):
    """Class to represent a LLM response in the feedback"""

    def __init__(self, user_ask: str, response: str):
        self.user_ask = user_ask
        self.response = response

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return self.__dict__


class FeedbackMetadata(FeedbackInfoBase):
    """Class to store feedback metadata."""

    def __init__(self):
        # In iteration mode, there can be multiple ask and response pairs.
        self.llm_responses = []
        self.llm = FeedbackLLM("", 0)

    def add_llm_response(self, user_ask: str, response: str) -> None:
        """Add a LLM response to the metadata."""
        llm_response = FeedbackLLMResponse(user_ask, response)
        self.llm_responses.append(llm_response)

    def update_llm(self, llm: LLM) -> None:
        """Update the LLM information in the metadata."""
        self.llm.update_from_llm(llm)

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "llm_responses": [resp.to_dict() for resp in self.llm_responses],
            "llm": self.llm.to_dict(),
        }


class UserFeedback(FeedbackInfoBase):
    """Class to store user rate and comment to the AI response."""

    def __init__(self, is_positive: bool, comment: Optional[str]):
        self.is_positive = is_positive
        self.comment = comment

    @property
    def rating_text(self) -> str:
        """Return human-readable rating text."""
        return "useful" if self.is_positive else "not useful"

    @property
    def rating_emoji(self) -> str:
        """Return emoji representation of the rating."""
        return "👍" if self.is_positive else "👎"

    def __str__(self) -> str:
        """Return string representation of the feedback."""
        if self.comment:
            return f"Rating: {self.rating_text}. Comment: {self.comment}"
        else:
            return f"Rating: {self.rating_text}. No additional comment."

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "is_positive": self.is_positive,
            "comment": self.comment,
        }


class Feedback(FeedbackInfoBase):
    """Class to store overall feedback data used to evaluate the AI response."""

    def __init__(self):
        self.metadata = FeedbackMetadata()
        self.user_feedback: Optional[UserFeedback] = None

    def set_user_feedback(self, user_feedback: UserFeedback) -> None:
        """Set the user feedback."""
        self.user_feedback = user_feedback

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "metadata": self.metadata.to_dict(),
            "user_feedback": self.user_feedback.to_dict()
            if self.user_feedback
            else None,
        }


FeedbackCallback = Callable[[Feedback], None]


def feedback_callback_example(feedback: Feedback) -> None:
    """
    Example implementation of a feedback callback function.

    This function demonstrates how to process feedback data using to_dict() methods
    and could be used for:
    - Logging feedback to files or databases
    - Sending feedback to analytics services
    - Training data collection
    - User satisfaction monitoring

    Args:
        feedback: Feedback object containing user feedback and metadata
    """
    print("\n=== Feedback Received ===")

    # Convert entire feedback to dict first - this is the main data structure
    feedback_dict = feedback.to_dict()
    print(f"Complete feedback dictionary keys: {list(feedback_dict.keys())}")

    # How to check user feedback using to_dict()
    print("\n1. Checking User Feedback:")
    user_feedback_dict = (
        feedback.user_feedback.to_dict() if feedback.user_feedback else None
    )
    if user_feedback_dict:
        print(f"   User feedback dict: {user_feedback_dict}")
        print(f"   Is positive: {user_feedback_dict['is_positive']}")
        print(f"   Comment: {user_feedback_dict['comment'] or 'None'}")
        # You can also access properties through the object:
        print(f"   Rating emoji: {feedback.user_feedback.rating_emoji}")  # type: ignore
        print(f"   Rating text: {feedback.user_feedback.rating_text}")  # type: ignore
    else:
        print("   No user feedback provided (user_feedback is None)")

    # How to check LLM information using to_dict()
    print("\n2. Checking LLM Information:")
    metadata_dict = feedback.metadata.to_dict()
    llm_dict = metadata_dict["llm"]
    print(f"   LLM dict: {llm_dict}")
    print(f"   Model: {llm_dict['model']}")
    print(f"   Max context size: {llm_dict['max_context_size']}")

    # How to check ask and response pairs using to_dict()
    print("\n3. Checking Ask and Response History:")
    llm_responses_dict = metadata_dict["llm_responses"]
    print(f"   Number of exchanges: {len(llm_responses_dict)}")

    for i, response_dict in enumerate(llm_responses_dict, 1):
        print(f"   Exchange {i} dict: {list(response_dict.keys())}")
        user_ask = response_dict["user_ask"]
        ai_response = response_dict["response"]
        print(f"     User ask: {user_ask}")
        print(f"     AI response: {ai_response}")

    print("=== End Feedback ===\n")
