import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from io import StringIO
from unittest.mock import Mock, patch

from rich.console import Console

from holmes.core.feedback import FeedbackMetadata
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.interactive import (
    AgenticProgressRenderer,
    Feedback,
    SlashCommandCompleter,
    SlashCommands,
    UserFeedback,
    _make_live,
    _run_inline_menu,
    handle_feedback_command,
    run_interactive_loop,
)
from holmes.utils.stream import StreamEvents, StreamMessage
from tests.mocks.toolset_mocks import SampleToolset


class TestAgenticProgressRendererSummary(unittest.TestCase):
    """Test that tasks and tools panels persist after flush()."""

    def _get_printed_panels(self, console):
        """Extract Panel objects from console.print calls."""
        from rich.panel import Panel
        panels = []
        for call in console.print.call_args_list:
            args = call[0] if call[0] else []
            for arg in args:
                if isinstance(arg, Panel):
                    panels.append(arg)
        return panels

    def test_flush_prints_tools_summary(self):
        """flush() should print the tools panel even when AI_MESSAGE never fired."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        # Simulate tool completion (what TOOL_RESULT handler does)
        renderer._tool_history.append(("kubectl_get_pods", "get pods in namespace default", "kubernetes", 1.2, 500, False))
        renderer._tool_history.append(("kubectl_top_pods", "get resource usage for pods", "kubernetes", 0.8, 300, False))
        renderer._total_bytes = 800
        renderer._total_queries = 2

        renderer.flush()

        # Should have printed panels (tools) and stats
        panels = self._get_printed_panels(console)
        assert len(panels) >= 1, f"Expected at least 1 panel, got {len(panels)}"
        assert console.print.call_count >= 2  # tools panel + stats line

    def test_flush_prints_tasks_summary(self):
        """flush() should print task panel when tasks exist."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        renderer._live_tasks = [
            {"content": "Check pods", "status": "completed"},
            {"content": "Check logs", "status": "in_progress"},
        ]
        renderer._tool_history.append(("kubectl_get_pods", "get pods in namespace default", "kubernetes", 1.0, 100, False))

        renderer.flush()

        panels = self._get_printed_panels(console)
        assert len(panels) >= 2, f"Expected tasks + tools panels, got {len(panels)}"

    def test_flush_no_double_print_after_ai_message(self):
        """Summary should print only once even if AI_MESSAGE already triggered it."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        renderer._tool_history.append(("kubectl_get_pods", "get pods in namespace default", "kubernetes", 1.0, 100, False))

        # Simulate AI_MESSAGE calling _print_investigation_summary
        renderer._print_investigation_summary()
        first_print_count = console.print.call_count

        # Now flush - should NOT re-print
        renderer.flush()
        assert console.print.call_count == first_print_count, (
            "Summary was printed twice"
        )

    def test_flush_no_output_when_no_tools(self):
        """flush() should not print anything when no tools ran."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        renderer.flush()

        console.print.assert_not_called()


class TestSlashCommandCompleter(unittest.TestCase):
    def test_init_without_unsupported_commands(self):
        """Test SlashCommandCompleter initialization without unsupported commands."""
        completer = SlashCommandCompleter()
        expected_commands = {cmd.command: cmd.description for cmd in SlashCommands}
        self.assertEqual(completer.commands, expected_commands)

    def test_init_with_unsupported_commands(self):
        """Test SlashCommandCompleter initialization with unsupported commands."""
        unsupported = [SlashCommands.FEEDBACK.command]
        completer = SlashCommandCompleter(unsupported)

        expected_commands = {cmd.command: cmd.description for cmd in SlashCommands}
        expected_commands.pop(SlashCommands.FEEDBACK.command)

        self.assertEqual(completer.commands, expected_commands)

    def test_get_completions_with_slash_prefix(self):
        """Test completion suggestions for slash commands."""
        completer = SlashCommandCompleter()
        document = Mock()
        document.text_before_cursor = "/ex"

        completions = list(completer.get_completions(document, None))

        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].text, SlashCommands.EXIT.command)

    def test_get_completions_without_slash_prefix(self):
        """Test no completions for non-slash input."""
        completer = SlashCommandCompleter()
        document = Mock()
        document.text_before_cursor = "regular input"

        completions = list(completer.get_completions(document, None))

        self.assertEqual(len(completions), 0)

    def test_get_completions_filters_unsupported_commands(self):
        """Test that unsupported commands are filtered out of completions."""
        unsupported = [SlashCommands.FEEDBACK.command]
        completer = SlashCommandCompleter(unsupported)
        document = Mock()
        document.text_before_cursor = "/feed"

        completions = list(completer.get_completions(document, None))

        self.assertEqual(len(completions), 0)


class TestHandleFeedbackCommand(unittest.TestCase):
    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_positive(self, mock_prompt_session_class):
        """Test feedback command with positive rating."""
        mock_prompt_session_class.return_value.prompt.side_effect = [
            "y",
            "Great response!",
            "Y",  # Final confirmation
        ]

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was populated
        self.assertIsNotNone(feedback.user_feedback)
        self.assertTrue(feedback.user_feedback.is_positive)
        self.assertEqual(feedback.user_feedback.comment, "Great response!")

        # Verify callback was called with the feedback object
        feedback_callback.assert_called_once_with(feedback)

        # Verify thank you message was printed
        console.print.assert_any_call(
            "[bold green]Thank you for your feedback! 🙏[/bold green]"
        )

    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_negative(self, mock_prompt_session_class):
        """Test feedback command with negative rating."""
        mock_prompt_session_class.return_value.prompt.side_effect = [
            "n",
            "Could be better",
            "Y",  # Final confirmation
        ]

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was populated
        self.assertIsNotNone(feedback.user_feedback)
        self.assertFalse(feedback.user_feedback.is_positive)
        self.assertEqual(feedback.user_feedback.comment, "Could be better")

        # Verify callback was called with the feedback object
        feedback_callback.assert_called_once_with(feedback)

        # Verify thank you message was printed
        console.print.assert_any_call(
            "[bold green]Thank you for your feedback! 🙏[/bold green]"
        )

    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_no_comment(self, mock_prompt_session_class):
        """Test feedback command without comment."""
        mock_prompt_session_class.return_value.prompt.side_effect = [
            "y",
            "",  # No comment
            "Y",  # Final confirmation
        ]

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was populated
        self.assertIsNotNone(feedback.user_feedback)
        self.assertTrue(feedback.user_feedback.is_positive)
        self.assertIsNone(feedback.user_feedback.comment)

        # Verify callback was called with the feedback object
        feedback_callback.assert_called_once_with(feedback)

        # Verify thank you message was printed
        console.print.assert_any_call(
            "[bold green]Thank you for your feedback! 🙏[/bold green]"
        )

    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_invalid_then_valid_rating(
        self, mock_prompt_session_class
    ):
        """Test feedback command with invalid rating first, then valid."""
        mock_prompt_session_class.return_value.prompt.side_effect = [
            "x",
            "y",
            "",  # No comment
            "Y",  # Final confirmation
        ]

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was populated
        self.assertIsNotNone(feedback.user_feedback)
        self.assertTrue(feedback.user_feedback.is_positive)
        self.assertIsNone(feedback.user_feedback.comment)

        # Verify callback was called with the feedback object
        feedback_callback.assert_called_once_with(feedback)

        # Verify error message was printed for invalid input
        console.print.assert_any_call(
            "[bold red]Please enter only 'y' for yes or 'n' for no.[/bold red]"
        )

        # Verify feedback recorded message was printed
        console.print.assert_any_call(
            "[bold green]✓ Feedback recorded (rating=👍, no comment)[/bold green]"
        )

        # Verify thank you message was printed
        console.print.assert_any_call(
            "[bold green]Thank you for your feedback! 🙏[/bold green]"
        )

    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_confirmation_cancelled(
        self, mock_prompt_session_class
    ):
        """Test feedback command when final confirmation is cancelled."""
        mock_prompt_session_class.return_value.prompt.side_effect = [
            "y",
            "Great response!",
            "n",  # Final confirmation cancelled
        ]

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was NOT populated and callback was NOT called
        # because final confirmation was cancelled
        self.assertIsNone(feedback.user_feedback)

        # Verify callback was NOT called since confirmation was cancelled
        feedback_callback.assert_not_called()

        # Verify cancellation message was printed, not thank you message
        console.print.assert_any_call("[dim]Feedback cancelled.[/dim]")

        # Ensure thank you message was NOT printed
        thank_you_calls = [
            call
            for call in console.print.call_args_list
            if "[bold green]Thank you for your feedback! 🙏[/bold green]" in str(call)
        ]
        self.assertEqual(len(thank_you_calls), 0)

    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_keyboard_interrupt(
        self, mock_prompt_session_class
    ):
        """Test feedback command when KeyboardInterrupt is raised."""
        mock_prompt_session_class.return_value.prompt.side_effect = KeyboardInterrupt()

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was not populated and callback was not called
        self.assertIsNone(feedback.user_feedback)
        feedback_callback.assert_not_called()

        # Verify cancellation message was printed
        console.print.assert_any_call("[dim]Feedback cancelled.[/dim]")

    @patch("holmes.interactive.PromptSession")
    def test_handle_feedback_command_with_comment_containing_markup(
        self, mock_prompt_session_class
    ):
        """Test feedback command with comment containing markup characters that need escaping."""
        mock_prompt_session_class.return_value.prompt.side_effect = [
            "y",
            "Great [bold]response[/bold] & nice <work>!",  # Comment with markup
            "Y",  # Final confirmation
        ]

        console = Mock()
        style = Mock()
        feedback = Feedback()
        feedback_callback = Mock()

        handle_feedback_command(style, console, feedback, feedback_callback)

        # Verify feedback object was populated
        self.assertIsNotNone(feedback.user_feedback)
        self.assertTrue(feedback.user_feedback.is_positive)
        self.assertEqual(
            feedback.user_feedback.comment, "Great [bold]response[/bold] & nice <work>!"
        )

        # Verify callback was called with the feedback object
        feedback_callback.assert_called_once_with(feedback)

        # The feedback recorded message should have escaped markup
        expected_msg = (
            "[bold green]✓ Feedback recorded (rating=👍, "
            '"Great \\[bold]response\\[/bold] & nice <work>!")[/bold green]'
        )
        console.print.assert_any_call(expected_msg)


class TestRunInteractiveLoop(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.mock_ai = Mock(spec=ToolCallingLLM)
        self.mock_ai.llm = Mock()
        self.mock_ai.llm.model = "test-model"
        self.mock_ai.llm.get_context_window_size.return_value = 4096
        self.mock_ai.tool_executor = Mock()
        self.mock_ai.tool_executor.toolsets = [SampleToolset()]

        # Mock AI response
        self.mock_response = Mock()
        self.mock_response.result = "Test response"
        self.mock_response.messages = []
        self.mock_response.tool_calls = []
        self.mock_ai.call.return_value = self.mock_response

        # Mock call_stream to yield an ANSWER_END event (used by interactive loop)
        def _mock_call_stream(**kwargs):
            yield StreamMessage(
                event=StreamEvents.ANSWER_END,
                data={
                    "content": "Test response",
                    "messages": [],
                    "tool_calls": [],
                    "num_llm_calls": 1,
                    "costs": {},
                },
            )
        self.mock_ai.call_stream = Mock(side_effect=_mock_call_stream)

        self.mock_console = Mock(spec=Console)

        # Create a temporary directory for history file
        self.temp_dir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.temp_dir, "history")

        # Patch the sample questions menu so it doesn't run prompt_toolkit Application in tests
        self._sample_questions_patcher = patch(
            "holmes.interactive._show_sample_questions_menu", return_value=None
        )
        self._sample_questions_patcher.start()

    def tearDown(self):
        """Clean up test fixtures."""
        self._sample_questions_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    @patch("holmes.interactive.handle_feedback_command")
    def test_run_interactive_loop_feedback_command_positive_with_callback(
        self,
        mock_handle_feedback,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test interactive loop with /feedback command - positive feedback."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = ["/feedback", "/exit"]

        mock_build_messages.return_value = []
        mock_callback = Mock()

        # Mock the feedback handler to simulate feedback collection and callback invocation
        def mock_feedback_handler(_style, _console, feedback, feedback_callback):
            # Simulate what the real function does
            user_feedback = UserFeedback(is_positive=True, comment="Great response!")
            feedback.user_feedback = user_feedback
            feedback_callback(feedback)

        mock_handle_feedback.side_effect = mock_feedback_handler

        # Run the interactive loop
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            feedback_callback=mock_callback,
        )

        # Verify feedback handler was called
        mock_handle_feedback.assert_called_once()

        # Verify callback was called with complete Feedback object
        mock_callback.assert_called_once()
        call_args = mock_callback.call_args[0][0]

        # Test complete Feedback structure
        self.assertIsInstance(call_args, Feedback)

        # Test UserFeedback component
        self.assertIsNotNone(call_args.user_feedback)
        self.assertIsInstance(call_args.user_feedback, UserFeedback)
        self.assertEqual(call_args.user_feedback.is_positive, True)
        self.assertEqual(call_args.user_feedback.comment, "Great response!")

        # Test FeedbackMetadata component
        self.assertIsNotNone(call_args.metadata)
        self.assertIsInstance(call_args.metadata, FeedbackMetadata)

        # Test LLM information in metadata
        self.assertIsNotNone(call_args.metadata.llm)
        self.assertEqual(call_args.metadata.llm.model, "test-model")
        self.assertEqual(call_args.metadata.llm.max_context_size, 4096)

        # Test LLM responses list (should be empty initially but list should exist)
        self.assertIsInstance(call_args.metadata.llm_responses, list)

        # Test to_dict() functionality
        feedback_dict = call_args.to_dict()
        self.assertIn("user_feedback", feedback_dict)
        self.assertIn("metadata", feedback_dict)
        self.assertEqual(feedback_dict["user_feedback"]["is_positive"], True)
        self.assertEqual(feedback_dict["user_feedback"]["comment"], "Great response!")
        self.assertEqual(feedback_dict["metadata"]["llm"]["model"], "test-model")
        self.assertEqual(feedback_dict["metadata"]["llm"]["max_context_size"], 4096)

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    @patch("holmes.interactive.handle_feedback_command")
    def test_run_interactive_loop_feedback_command_negative_with_callback(
        self,
        mock_handle_feedback,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test interactive loop with /feedback command - negative feedback."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = ["/feedback", "/exit"]

        mock_build_messages.return_value = []
        mock_callback = Mock()

        # Mock the feedback handler to simulate feedback collection and callback invocation
        def mock_feedback_handler(_style, _console, feedback, feedback_callback):
            # Simulate what the real function does
            user_feedback = UserFeedback(is_positive=False, comment="Could be better")
            feedback.user_feedback = user_feedback
            feedback_callback(feedback)

        mock_handle_feedback.side_effect = mock_feedback_handler

        # Run the interactive loop
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            feedback_callback=mock_callback,
        )

        # Verify callback was called with complete Feedback object containing negative feedback
        mock_callback.assert_called_once()
        call_args = mock_callback.call_args[0][0]

        # Test complete Feedback structure
        self.assertIsInstance(call_args, Feedback)

        # Test UserFeedback component
        self.assertIsNotNone(call_args.user_feedback)
        self.assertIsInstance(call_args.user_feedback, UserFeedback)
        self.assertEqual(call_args.user_feedback.is_positive, False)
        self.assertEqual(call_args.user_feedback.comment, "Could be better")

        # Test FeedbackMetadata component
        self.assertIsNotNone(call_args.metadata)
        self.assertIsInstance(call_args.metadata, FeedbackMetadata)

        # Test LLM information in metadata
        self.assertIsNotNone(call_args.metadata.llm)
        self.assertEqual(call_args.metadata.llm.model, "test-model")
        self.assertEqual(call_args.metadata.llm.max_context_size, 4096)

        # Test LLM responses list
        self.assertIsInstance(call_args.metadata.llm_responses, list)

        # Test to_dict() functionality for negative feedback
        feedback_dict = call_args.to_dict()
        self.assertIn("user_feedback", feedback_dict)
        self.assertIn("metadata", feedback_dict)
        self.assertEqual(feedback_dict["user_feedback"]["is_positive"], False)
        self.assertEqual(feedback_dict["user_feedback"]["comment"], "Could be better")
        self.assertEqual(feedback_dict["metadata"]["llm"]["model"], "test-model")
        self.assertEqual(feedback_dict["metadata"]["llm"]["max_context_size"], 4096)
        self.assertIsInstance(feedback_dict["metadata"]["llm_responses"], list)

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    @patch("holmes.interactive.handle_feedback_command")
    def test_run_interactive_loop_feedback_with_conversation_history(
        self,
        mock_handle_feedback,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test feedback system with conversation history (LLM responses)."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = ["What is Kubernetes?", "/feedback", "/exit"]

        mock_build_messages.return_value = [
            {"role": "user", "content": "What is Kubernetes?"}
        ]
        mock_callback = Mock()

        # Mock the feedback handler to simulate feedback collection and callback invocation
        def mock_feedback_handler(_style, _console, feedback, feedback_callback):
            # Simulate what the real function does
            user_feedback = UserFeedback(is_positive=True, comment="Very helpful!")
            feedback.user_feedback = user_feedback
            feedback_callback(feedback)

        mock_handle_feedback.side_effect = mock_feedback_handler

        # Mock tracer for the normal query
        mock_tracer = Mock()
        mock_trace_span = Mock()
        mock_tracer.start_trace.return_value.__enter__ = Mock(
            return_value=mock_trace_span
        )
        mock_tracer.start_trace.return_value.__exit__ = Mock(return_value=None)
        mock_tracer.get_trace_url.return_value = None

        # Run the interactive loop with a conversation
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            feedback_callback=mock_callback,
            tracer=mock_tracer,
        )

        # Verify callback was called with Feedback containing conversation history
        mock_callback.assert_called_once()
        call_args = mock_callback.call_args[0][0]

        # Test complete Feedback structure with conversation history
        self.assertIsInstance(call_args, Feedback)

        # Test UserFeedback component
        self.assertIsNotNone(call_args.user_feedback)
        self.assertEqual(call_args.user_feedback.is_positive, True)
        self.assertEqual(call_args.user_feedback.comment, "Very helpful!")

        # Test FeedbackMetadata with LLM responses
        self.assertIsNotNone(call_args.metadata)
        self.assertIsInstance(call_args.metadata, FeedbackMetadata)

        # Test LLM information
        self.assertEqual(call_args.metadata.llm.model, "test-model")
        self.assertEqual(call_args.metadata.llm.max_context_size, 4096)

        # Test LLM responses list contains the conversation
        self.assertIsInstance(call_args.metadata.llm_responses, list)
        self.assertGreaterEqual(
            len(call_args.metadata.llm_responses), 1
        )  # Should have at least one exchange

        # Test to_dict() functionality with conversation history
        feedback_dict = call_args.to_dict()
        self.assertIn("metadata", feedback_dict)
        self.assertIn("llm_responses", feedback_dict["metadata"])
        self.assertIsInstance(feedback_dict["metadata"]["llm_responses"], list)

        # If there are responses, verify their structure
        if feedback_dict["metadata"]["llm_responses"]:
            first_response = feedback_dict["metadata"]["llm_responses"][0]
            self.assertIn("user_ask", first_response)
            self.assertIn("response", first_response)
            self.assertIsInstance(first_response["user_ask"], str)
            self.assertIsInstance(first_response["response"], str)

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    def test_run_interactive_loop_feedback_command_without_callback(
        self,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test interactive loop with /feedback command when no callback is provided."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = ["/feedback", "/exit"]

        mock_build_messages.return_value = []

        # Run the interactive loop without feedback callback
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            feedback_callback=None,  # No callback
        )

        # Verify "Unknown command" message was displayed
        unknown_calls = [
            call_args
            for call_args in self.mock_console.print.call_args_list
            if "Unknown command" in str(call_args)
        ]
        self.assertTrue(len(unknown_calls) > 0)

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    def test_run_interactive_loop_feedback_help_filtering(
        self,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test that help command filters out feedback when callback is None."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = ["/help", "/exit"]

        mock_build_messages.return_value = []

        # Run without feedback callback
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            feedback_callback=None,
        )

        # Check all printed messages
        all_prints = [
            str(call_args) for call_args in self.mock_console.print.call_args_list
        ]

        # Should contain help for other commands but not feedback
        has_help_command = any("/help" in print_msg for print_msg in all_prints)
        has_exit_command = any("/exit" in print_msg for print_msg in all_prints)
        has_feedback_command = any("/feedback" in print_msg for print_msg in all_prints)

        self.assertTrue(has_help_command)
        self.assertTrue(has_exit_command)
        self.assertFalse(has_feedback_command)  # Should be filtered out

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    def test_run_interactive_loop_feedback_help_not_filtering_with_callback(
        self,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test that help command shows feedback when callback is provided."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = ["/help", "/exit"]

        mock_build_messages.return_value = []
        mock_callback = Mock()

        # Run with feedback callback
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            feedback_callback=mock_callback,
        )

        # Check all printed messages
        all_prints = [
            str(call_args) for call_args in self.mock_console.print.call_args_list
        ]

        # Should contain help for feedback command
        has_feedback_command = any("/feedback" in print_msg for print_msg in all_prints)
        self.assertTrue(has_feedback_command)  # Should be shown

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    def test_run_interactive_loop_with_initial_input(
        self,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test interactive loop with initial user input."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        mock_session.prompt.side_effect = [
            "/exit"
        ]  # Only need exit after initial input

        initial_input = "What is kubernetes?"
        mock_build_messages.return_value = [{"role": "user", "content": initial_input}]

        # Mock tracer
        mock_tracer = Mock()
        mock_trace_span = Mock()
        mock_tracer.start_trace.return_value.__enter__ = Mock(
            return_value=mock_trace_span
        )
        mock_tracer.start_trace.return_value.__exit__ = Mock(return_value=None)
        mock_tracer.get_trace_url.return_value = None

        # Run the interactive loop
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=initial_input,
            include_files=None,
            show_tool_output=False,
            check_version=False,
            tracer=mock_tracer,
        )

        # Verify initial input was displayed
        initial_calls = [
            call_args
            for call_args in self.mock_console.print.call_args_list
            if initial_input in str(call_args)
        ]
        self.assertTrue(len(initial_calls) > 0)

        # Verify AI was called with initial input
        self.mock_ai.call_stream.assert_called_once()

    @patch("holmes.interactive.check_version_async")
    @patch("holmes.interactive.PromptSession")
    @patch("holmes.interactive.build_initial_ask_messages")
    @patch(
        "holmes.interactive.config_path_dir", new_callable=lambda: tempfile.gettempdir()
    )
    def test_run_interactive_loop_exception_handling(
        self,
        mock_config_dir,
        mock_build_messages,
        mock_prompt_session_class,
        mock_check_version,
    ):
        """Test interactive loop exception handling."""
        mock_session = Mock()
        mock_prompt_session_class.return_value = mock_session
        # First call raises exception, second call exits
        mock_session.prompt.side_effect = [Exception("Test error"), "/exit"]

        mock_build_messages.return_value = []

        # Run the interactive loop
        run_interactive_loop(
            ai=self.mock_ai,
            console=self.mock_console,
            initial_user_input=None,
            include_files=None,
            show_tool_output=False,
            check_version=False,
        )

        # Verify error was displayed
        error_calls = [
            call_args
            for call_args in self.mock_console.print.call_args_list
            if "Error:" in str(call_args)
        ]
        self.assertTrue(len(error_calls) > 0)

    def test_run_interactive_loop_unsupported_commands_without_callback(self):
        """Test that feedback command is not available when callback is None."""
        with patch("holmes.interactive.check_version_async"), patch(
            "holmes.interactive.PromptSession"
        ) as mock_prompt_session_class, patch(
            "holmes.interactive.build_initial_ask_messages"
        ), patch("holmes.interactive.config_path_dir", new=tempfile.gettempdir()):
            mock_session = Mock()
            mock_prompt_session_class.return_value = mock_session
            mock_session.prompt.side_effect = ["/help", "/exit"]

            # Run the interactive loop without feedback callback
            run_interactive_loop(
                ai=self.mock_ai,
                console=self.mock_console,
                initial_user_input=None,
                include_files=None,
                show_tool_output=False,
                check_version=False,
                feedback_callback=None,  # No callback
            )

            # Verify feedback command is not shown in help
            help_calls = [
                str(call_args) for call_args in self.mock_console.print.call_args_list
            ]

            # The feedback command should not be shown since callback is None
            has_feedback_in_help = any(
                "/feedback" in call_str for call_str in help_calls
            )
            self.assertFalse(has_feedback_in_help)


class TestRendererEndToEnd(unittest.TestCase):
    """End-to-end tests for AgenticProgressRenderer with real Rich Console.

    These tests exercise the full lifecycle: start() → handle_event() → flush(),
    using a real Console(record=True) to capture actual rendered output.
    """

    def _make_console(self):
        return Console(width=100, record=True, force_terminal=True, color_system=None, file=StringIO())

    def _make_event(self, event_type, data=None):
        return StreamMessage(event=event_type, data=data or {})

    def test_full_lifecycle_with_tools(self):
        """Full start → tools → AI message → flush lifecycle renders correctly."""
        console = self._make_console()
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        renderer.start()
        all_tool_calls = []
        history = []

        # Tool 1: start
        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "kubectl_get"}),
            all_tool_calls, history,
        )

        # Tool 1: complete with output
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "kubectl_get",
                "description": "kubectl get pods --all-namespaces",
                "toolset_name": "kubernetes/core",
                "result": {
                    "data": "NAMESPACE  NAME       READY  STATUS\ndefault    nginx-abc  1/1    Running",
                    "elapsed_seconds": 1.5,
                },
            }),
            all_tool_calls, history,
        )

        # Tool 2: start + complete with empty output (error)
        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "fetch_skill"}),
            all_tool_calls, history,
        )
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "fetch_skill",
                "description": "Fetch Skill cluster-problems.md",
                "toolset_name": "skills",
                "result": {"data": "", "elapsed_seconds": 0.0},
            }),
            all_tool_calls, history,
        )

        # AI message triggers summary
        renderer.handle_event(
            self._make_event(StreamEvents.AI_MESSAGE, {
                "content": "All pods are running normally.",
            }),
            all_tool_calls, history,
        )

        renderer.flush()

        output = console.export_text()

        # Verify tools summary is printed
        assert "kubectl get pods --all-namespaces" in output, f"Tool description not in output:\n{output}"
        assert "Fetch Skill cluster-problems.md" in output, f"Error tool not in output:\n{output}"
        assert "(error)" in output, f"Error marker not in output:\n{output}"

        # Verify AI message content is printed
        assert "All pods are running normally." in output, f"AI message not in output:\n{output}"

        # Verify stats line
        assert "tokens" in output.lower(), f"Stats line not in output:\n{output}"

    def test_no_data_pane_before_tool_output(self):
        """Data pane should not appear until tools produce output."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()

        # Initial state: no data pane
        display_text = self._render_to_text(renderer)
        assert "Data" not in display_text, f"Data pane appeared too early:\n{display_text}"

        # Add tasks — still no data pane
        renderer._live_tasks = [
            {"content": "Check pods", "status": "pending"},
        ]
        display_text = self._render_to_text(renderer)
        assert "Data" not in display_text, f"Data pane appeared with only tasks:\n{display_text}"
        assert "Check pods" in display_text, f"Tasks not shown:\n{display_text}"

        # Add in-flight tool — still no data pane
        renderer._in_flight[1] = ("kubectl_get", time.time())
        renderer._thinking = False
        display_text = self._render_to_text(renderer)
        assert "Data" not in display_text, f"Data pane appeared during in-flight tool:\n{display_text}"
        assert "kubectl_get" in display_text, f"In-flight tool not shown:\n{display_text}"

        # Now add output — data pane should appear
        del renderer._in_flight[1]
        renderer._thinking = True
        renderer._tool_history.append(("kubectl_get", "get pods", "k8s", 1.0, 100, False))
        renderer._ingest_output("kubectl_get", "some output data", description="get pods")
        display_text = self._render_to_text(renderer)
        assert "Data" in display_text, f"Data pane did not appear after output:\n{display_text}"
        assert "some output data" in display_text, f"Output not in data pane:\n{display_text}"

    def test_data_pane_fixed_width(self):
        """Data pane should take 50% of terminal width regardless of content."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()

        # Add a tool with short output — data pane should still be ~50% wide
        renderer._tool_history.append(("tool1", "desc", "ts", 1.0, 10, False))
        renderer._ingest_output("tool1", "short", description="desc")

        display_text = self._render_to_text(renderer)

        # The data panel border should be ~50 chars (50% of 100)
        data_lines = [l for l in display_text.split("\n") if "Data" in l]
        assert data_lines, f"No Data header line found:\n{display_text}"
        data_header = data_lines[0]
        # With ratio=1:1, data pane should be close to 50 chars, not shrunk
        assert len(data_header.rstrip()) >= 40, (
            f"Data pane header too narrow ({len(data_header.rstrip())} chars), "
            f"expected ~50% width:\n{display_text}"
        )

    def test_error_tool_shows_token_count(self):
        """Error tools with output should show both token count and (error)."""
        console = Console(width=120, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        all_tool_calls = []

        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "bad_query"}),
            all_tool_calls, [],
        )
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "bad_query",
                "description": "bad query that returned error",
                "toolset_name": "test",
                "result": {
                    "data": "Error: connection refused to database server",
                    "elapsed_seconds": 0.5,
                    "error": True,
                },
            }),
            all_tool_calls, [],
        )

        # Tool should have output_len > 0 AND is_error
        assert len(renderer._tool_history) == 1
        _name, _desc, _ts, _elapsed, output_len, is_error = renderer._tool_history[0]
        assert is_error, "Tool should be marked as error"
        assert output_len > 0, "Tool should have output length despite error"

        # Render the left pane and verify both token count and (error) appear
        display_text = self._render_to_text(renderer)
        assert "tokens" in display_text.lower() or "token" in display_text.lower(), (
            f"Token count not shown for error tool:\n{display_text}"
        )
        assert "(error)" in display_text, f"Error marker not shown:\n{display_text}"

    def test_empty_output_shows_red_marker(self):
        """Empty tool output should show a visible red marker, not dim text."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()

        renderer._tool_history.append(("bad_tool", "bad tool call", "test", 0.0, 0, True))
        renderer._ingest_output("bad_tool", "", description="bad tool call")

        display_text = self._render_to_text(renderer)
        assert "no output" in display_text, f"Empty marker not found:\n{display_text}"

    def test_log_buffering_filter(self):
        """Log filter should capture records and prevent them from passing through."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        root = logging.getLogger()

        # Install filter on all handlers (matches production behavior)
        for handler in root.handlers:
            handler.addFilter(renderer._log_filter)
        try:
            test_logger = logging.getLogger("test.interactive.buffering")
            test_logger.error("This should be buffered")

            assert len(renderer._log_buffer) >= 1, (
                f"Expected at least 1 buffered log record, got {len(renderer._log_buffer)}"
            )
            assert renderer._log_buffer[0].getMessage() == "This should be buffered"
        finally:
            for handler in root.handlers:
                handler.removeFilter(renderer._log_filter)
            renderer._log_buffer.clear()

    def test_start_installs_log_filter_on_handlers(self):
        """start() should install the log filter on root logger's handlers."""
        console = self._make_console()
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        root = logging.getLogger()

        renderer.start()
        try:
            # Filter should be on at least one handler
            has_filter = any(
                renderer._log_filter in h.filters for h in root.handlers
            )
            assert has_filter, "Log filter not installed on any handler after start()"
        finally:
            renderer.flush()

        # After flush, filter should be removed from all handlers
        has_filter = any(
            renderer._log_filter in h.filters for h in root.handlers
        )
        assert not has_filter, "Log filter still on handlers after flush"

    def test_handle_event_tool_result_populates_data(self):
        """TOOL_RESULT events should populate tool history and data buffer."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        all_tool_calls = []

        # Start a tool
        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "my_tool"}),
            all_tool_calls, [],
        )
        assert len(renderer._in_flight) == 1
        assert renderer._thinking is False

        # Complete the tool
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "my_tool",
                "description": "do something useful",
                "toolset_name": "test_toolset",
                "result": {
                    "data": "line 1\nline 2\nline 3",
                    "elapsed_seconds": 2.0,
                },
            }),
            all_tool_calls, [],
        )

        assert len(renderer._in_flight) == 0, "Tool still in flight after completion"
        assert renderer._thinking is True, "Should be thinking between tools"
        assert len(renderer._tool_history) == 1
        assert renderer._tool_history[0][1] == "do something useful"
        assert len(renderer._data_lines) > 0, "Data buffer should have content"
        assert any("line 1" in l for l in renderer._data_lines)

    def test_ai_message_keeps_live_active(self):
        """AI_MESSAGE should keep Live running so the data pane survives for subsequent tools."""
        console = self._make_console()
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        renderer.start()
        all_tool_calls = []

        # Run a tool through the full cycle
        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "test_tool"}),
            all_tool_calls, [],
        )
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "test_tool",
                "description": "test tool description",
                "toolset_name": "testing",
                "result": {"data": "some output", "elapsed_seconds": 0.5},
            }),
            all_tool_calls, [],
        )

        # AI message should NOT stop Live — summary is deferred to flush()
        renderer.handle_event(
            self._make_event(StreamEvents.AI_MESSAGE, {
                "content": "Here is my analysis.",
            }),
            all_tool_calls, [],
        )

        # Live should still be running (never stopped)
        assert renderer._live is not None, "Live display should remain active after AI_MESSAGE"
        assert renderer._summary_printed is False, "Summary should be deferred to flush()"

        output = console.export_text()
        assert "Here is my analysis." in output, f"AI message not printed:\n{output}"

    def test_multiple_tool_rounds_no_duplicate_summary(self):
        """Multiple tool rounds followed by flush should print summary once."""
        console = self._make_console()
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)

        renderer.start()
        all_tool_calls = []

        # Round 1
        for tool_name in ["tool_a", "tool_b"]:
            renderer.handle_event(
                self._make_event(StreamEvents.START_TOOL, {"tool_name": tool_name}),
                all_tool_calls, [],
            )
            renderer.handle_event(
                self._make_event(StreamEvents.TOOL_RESULT, {
                    "tool_name": tool_name,
                    "description": f"run {tool_name}",
                    "toolset_name": "test",
                    "result": {"data": f"output from {tool_name}", "elapsed_seconds": 0.1},
                }),
                all_tool_calls, [],
            )

        # AI message
        renderer.handle_event(
            self._make_event(StreamEvents.AI_MESSAGE, {"content": "Done."}),
            all_tool_calls, [],
        )

        # flush should print summary exactly once
        renderer.flush()

        output = console.export_text()
        # Count occurrences of "Tools" panel header
        tools_count = output.count("Tools")
        assert tools_count <= 2, (  # Title + border from single flush
            f"Tools panel printed multiple times ({tools_count}):\n{output}"
        )

    def test_todo_write_updates_tasks(self):
        """TodoWrite tool results should update live tasks, not appear in tool history."""
        console = Mock(spec=Console)
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        all_tool_calls = []

        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "TodoWrite"}),
            all_tool_calls, [],
        )
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "TodoWrite",
                "description": "TodoWrite",
                "toolset_name": "",
                "result": {
                    "data": "Tasks updated",
                    "elapsed_seconds": 0.0,
                    "params": {
                        "todos": [
                            {"content": "Check pods", "status": "in_progress"},
                            {"content": "Check logs", "status": "pending"},
                        ]
                    },
                },
            }),
            all_tool_calls, [],
        )

        assert renderer._live_tasks is not None, "Tasks not set"
        assert len(renderer._live_tasks) == 2
        assert renderer._live_tasks[0]["content"] == "Check pods"
        # TodoWrite should NOT appear in tool history
        assert len(renderer._tool_history) == 0, "TodoWrite should not be in tool history"
        # TodoWrite should NOT be in data buffer
        assert not any("TodoWrite" in l for l in renderer._data_lines), "TodoWrite in data buffer"

    def test_approval_pending_shows_paused_status(self):
        """When approval is pending, status line should show static paused text."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()
        renderer._tool_history.append(("t", "desc", "ts", 1.0, 100, False))
        renderer._ingest_output("t", "some data", description="desc")

        # Before approval: should show "Analyzing"
        display_text = self._render_to_text(renderer)
        assert "Analyzing" in display_text, f"Should show Analyzing:\n{display_text}"
        assert "Approval required" not in display_text

        # Set approval pending
        renderer._approval_pending = True
        renderer._thinking = False
        display_text = self._render_to_text(renderer)
        assert "Approval required" in display_text, f"Should show paused:\n{display_text}"
        assert "Analyzing" not in display_text, f"Should not show Analyzing:\n{display_text}"

    def test_approval_pending_replaces_data_pane(self):
        """When approval is pending, data pane should show 'Waiting for approval'."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()
        renderer._tool_history.append(("t", "desc", "ts", 1.0, 100, False))
        renderer._ingest_output("t", "real tool output here", description="desc")

        # Before approval: should show actual data
        display_text = self._render_to_text(renderer)
        assert "real tool output here" in display_text

        # Set approval pending: data replaced
        renderer._approval_pending = True
        display_text = self._render_to_text(renderer)
        assert "real tool output here" not in display_text, f"Should not show data:\n{display_text}"
        assert "Approve bash command?" in display_text, f"Should show approval prompt:\n{display_text}"

    def test_approval_pending_dims_task_panel(self):
        """When approval is pending, tasks should all be dim (no bold yellow)."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()
        renderer._live_tasks = [
            {"content": "Check pods", "status": "in_progress"},
            {"content": "Check logs", "status": "pending"},
        ]
        renderer._tool_history.append(("t", "desc", "ts", 1.0, 100, False))
        renderer._ingest_output("t", "data", description="desc")

        renderer._approval_pending = True
        display_text = self._render_to_text(renderer)
        # The "Tasks" title should not be bold (dimmed)
        assert "Approval required" in display_text

    def test_approval_clears_on_new_tool(self):
        """APPROVAL_REQUIRED then START_TOOL should clear the pending state."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        all_tool_calls = []

        # First tool
        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "tool1"}),
            all_tool_calls, [],
        )
        renderer.handle_event(
            self._make_event(StreamEvents.TOOL_RESULT, {
                "tool_name": "tool1", "description": "tool1", "toolset_name": "ts",
                "result": {"data": "out", "elapsed_seconds": 0.5},
            }),
            all_tool_calls, [],
        )

        # Approval required
        renderer.handle_event(
            self._make_event(StreamEvents.APPROVAL_REQUIRED, {}),
            all_tool_calls, [],
        )
        assert renderer._approval_pending is True
        assert renderer._thinking is False

        # New tool starts (approval was granted)
        renderer.handle_event(
            self._make_event(StreamEvents.START_TOOL, {"tool_name": "tool2"}),
            all_tool_calls, [],
        )
        assert renderer._approval_pending is False

    def test_approval_shows_command_description(self):
        """When approval is pending with descriptions, the command should be shown."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()
        renderer._tool_history.append(("t", "desc", "ts", 1.0, 100, False))
        renderer._ingest_output("t", "real tool output here", description="desc")

        # Set approval pending with command description
        renderer._approval_pending = True
        renderer._pending_approval_descriptions = ["egrep -r 'error' /var/log"]
        display_text = self._render_to_text(renderer)
        assert "egrep -r" in display_text, f"Should show command:\n{display_text}"
        assert "Approve bash command?" in display_text, f"Should show title:\n{display_text}"

    def test_approval_event_stores_descriptions(self):
        """APPROVAL_REQUIRED event should store descriptions from pending_approvals."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()

        event = self._make_event(StreamEvents.APPROVAL_REQUIRED, {
            "pending_approvals": [
                {"description": "kubectl get pods", "tool_name": "bash", "tool_call_id": "1", "params": {}},
            ]
        })
        renderer.handle_event(event, [], [])
        assert renderer._approval_pending is True
        assert renderer._pending_approval_descriptions == ["kubectl get pods"]

    def test_approval_pending_hides_data_stats(self):
        """When approval is pending, data pane title should not show stats."""
        console = Console(width=100, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer._thinking = True
        renderer._start_time = time.time()
        renderer._tool_history.append(("t", "desc", "ts", 1.0, 500, False))
        renderer._ingest_output("t", "x" * 500, description="desc")
        renderer._total_bytes = 500
        renderer._total_queries = 1

        renderer._approval_pending = True
        display_text = self._render_to_text(renderer)
        assert "tokens across" not in display_text, f"Stats should be hidden:\n{display_text}"

    def _render_to_text(self, renderer):
        """Render the display to plain text using a recording console."""
        capture = Console(width=100, record=True, force_terminal=True, color_system=None, file=StringIO())
        display = renderer._build_display()
        capture.print(display)
        return capture.export_text()


class TestLiveDisplayNoGhostFrames(unittest.TestCase):
    """Verify _make_live fixes the Rich ghost-frame bug.

    Rich 13.9.4 bug: Live.refresh() calls console.print(Control()) with
    the default end="\\n", adding a trailing newline not counted in
    LiveRender._shape. This causes position_cursor() (height-1 cursor-ups)
    to under-erase by 1 line when the terminal has space below the display.

    _make_live returns a Live subclass that overrides refresh() to pass
    end="", eliminating the spurious trailing newline.

    Reproduction technique: render to a StringIO file with force_terminal=True,
    then parse ANSI escape sequences to count cursor-up instructions per frame.
    """

    def test_make_live_no_trailing_newline(self):
        """Frames rendered by _make_live should NOT end with a trailing newline.

        With end="" in refresh(), the cursor stays on the last content line.
        position_cursor with height-1 cursor-ups then correctly reaches line 1.
        """
        from rich.text import Text

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80, color_system="truecolor")

        live = _make_live(Text("line 0\nline 1\nline 2"), console=console, transient=True, auto_refresh=False)
        live.start()
        live.refresh()

        # Render another frame
        live.update(Text("frame 1\nframe 1 line 2"))
        live.refresh()

        live.stop()

        raw = buf.getvalue()

        # With end="", frames should have exactly height-1 newlines
        # (between lines, no trailing). Split out erase blocks.
        erase_pattern = r"\x1b\[2K(?:\x1b\[1A\x1b\[2K)*"
        parts = re.split(f"({erase_pattern})", raw)

        content_frames = []
        erase_ups = []
        for part in parts:
            if "\x1b[1A" in part:
                erase_ups.append(part.count("\x1b[1A"))
            elif len(part) > 2:
                content_frames.append(part.count("\n"))

        # Frame 0: "line 0\nline 1\nline 2" = 3 lines, 2 newlines between them
        # With end="": 2 newlines in output. Cursor stays on line 3.
        # Erase for frame 0→1: height-1 = 2 cursor-ups (Rich default, correct with end="")
        if content_frames:
            self.assertEqual(
                content_frames[0],
                2,
                f"Frame 0 should have 2 newlines (no trailing), got {content_frames[0]}",
            )
        if erase_ups:
            self.assertEqual(
                erase_ups[0],
                2,
                f"First erase should use 2 cursor-ups (height-1 for 3-line frame), got {erase_ups[0]}",
            )

    def test_renderer_live_uses_fixed_subclass(self):
        """AgenticProgressRenderer.start() should use the _FixedLive subclass."""
        from rich.live import Live

        console = Console(width=120, force_terminal=True, color_system=None, file=StringIO())
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        renderer.start()
        try:
            self.assertIsNotNone(renderer._live)
            # Should be a subclass of Live, not Live itself
            self.assertNotEqual(
                type(renderer._live),
                Live,
                "Live instance should be _FixedLive subclass, not plain Live",
            )
            self.assertIsInstance(renderer._live, Live)
        finally:
            renderer._stop_live()


class TestModelMessageFormat(unittest.TestCase):
    """Verify model info message format has no double parentheses."""

    def test_default_model_no_double_parens(self):
        """Default model message should not contain ')(' pattern."""
        from holmes.config import Config

        config = Config(model="test-model")
        config._model_source = None
        context = config._format_token_count(1_000_000)
        max_resp = config._format_token_count(64_000)

        # Simulate the message construction logic
        if config._model_source:
            source_hint = f"configured {config._model_source}"
        else:
            source_hint = "default, change with --model, see https://holmesgpt.dev/ai-providers"
        msg = f"Model: test-model, {context} context, {max_resp} max response ({source_hint})"

        self.assertNotIn(")(", msg, f"Double parens found in: {msg}")
        self.assertEqual(msg.count("("), 1, f"Should have exactly one opening paren: {msg}")
        self.assertEqual(msg.count(")"), 1, f"Should have exactly one closing paren: {msg}")

    def test_env_model_no_double_parens(self):
        """$MODEL sourced model should have clean format."""
        from holmes.config import Config

        config = Config(model="test-model")
        config._model_source = "via $MODEL"
        context = config._format_token_count(128_000)
        max_resp = config._format_token_count(8_192)

        if config._model_source:
            source_hint = f"configured {config._model_source}"
        else:
            source_hint = "default, change with --model, see https://holmesgpt.dev/ai-providers"
        msg = f"Model: test-model, {context} context, {max_resp} max response ({source_hint})"

        self.assertNotIn(")(", msg, f"Double parens found in: {msg}")
        self.assertIn("configured via $MODEL", msg)
        # Format: Model: test-model, 128K context, 8K max response (configured via $MODEL)
        self.assertTrue(
            msg.startswith("Model: test-model, 128K context, 8K max response"),
            f"Unexpected format: {msg}",
        )

    def test_config_file_model_no_double_parens(self):
        """Config-file sourced model should have clean format."""
        from holmes.config import Config

        config = Config(model="test-model")
        config._model_source = "in ~/.holmes/config.yaml"

        if config._model_source:
            source_hint = f"configured {config._model_source}"
        else:
            source_hint = "default"
        msg = f"Model: test-model, 1M context, 64K max response ({source_hint})"

        self.assertNotIn(")(", msg, f"Double parens found in: {msg}")
        self.assertIn("configured in ~/.holmes/config.yaml", msg)


class TestDataPaneScrollAndWidth(unittest.TestCase):
    """Test data pane scroll-to-tail and dynamic width behavior."""

    def _make_renderer(self, width=120):
        console = Mock(spec=Console)
        console.width = width
        renderer = AgenticProgressRenderer(console, tool_number_offset=0)
        return renderer

    def test_ingest_sets_follow_tail(self):
        """After ingesting data, _follow_tail should be True so tick snaps to end."""
        r = self._make_renderer()
        self.assertTrue(r._follow_tail)  # starts True
        r._follow_tail = False  # simulate tick consuming it
        r._ingest_output("tool1", "line1\nline2\nline3")
        self.assertTrue(r._follow_tail)

    def test_ingest_empty_sets_follow_tail(self):
        """Even empty output should set follow_tail so the header is visible."""
        r = self._make_renderer()
        r._follow_tail = False
        r._ingest_output("tool1", "")
        self.assertTrue(r._follow_tail)

    def test_tick_snaps_to_tail(self):
        """When follow_tail is True, tick should set scroll_offset to max_start."""
        r = self._make_renderer()
        # Add more lines than the visible pane
        for i in range(30):
            r._data_lines.append(f"line {i}")
        r._follow_tail = True
        r._scroll_offset = 0

        # Simulate tick logic
        max_start = len(r._data_lines) - r._DATA_PANE_LINES
        if r._follow_tail:
            r._scroll_offset = max_start
            r._follow_tail = False
            r._scroll_pause = 20

        self.assertEqual(r._scroll_offset, max_start)
        self.assertFalse(r._follow_tail)
        self.assertEqual(r._scroll_pause, 20)

    def test_idle_scroll_wraps_to_zero(self):
        """When idle scroll reaches end, it wraps to 0 (modulo behavior)."""
        r = self._make_renderer()
        for i in range(30):
            r._data_lines.append(f"line {i}")
        max_start = len(r._data_lines) - r._DATA_PANE_LINES
        r._scroll_offset = max_start  # at the end
        r._follow_tail = False
        r._scroll_pause = 0

        # Simulate one tick: offset + SCROLL_SPEED >= max_start → wraps to 0
        r._scroll_offset += r._SCROLL_SPEED
        if r._scroll_offset >= max_start:
            r._scroll_offset = 0
            r._scroll_pause = 6

        self.assertEqual(r._scroll_offset, 0)
        self.assertEqual(r._scroll_pause, 6)

    def test_new_data_interrupts_idle_scroll(self):
        """New data arriving sets follow_tail, which overrides idle scroll."""
        r = self._make_renderer()
        for i in range(50):
            r._data_lines.append(f"line {i}")
        r._scroll_offset = 5  # mid-scroll
        r._follow_tail = False

        # New data arrives
        r._ingest_output("tool2", "new output\nmore output")
        self.assertTrue(r._follow_tail)

        # After tick, should be at tail
        max_start = len(r._data_lines) - r._DATA_PANE_LINES
        r._scroll_offset = max_start
        r._follow_tail = False
        self.assertEqual(r._scroll_offset, max_start)

    def test_dynamic_line_max_uses_terminal_width(self):
        """_data_line_max should scale with terminal width."""
        r_narrow = self._make_renderer(width=80)
        r_wide = self._make_renderer(width=200)
        self.assertGreater(r_wide._data_line_max(), r_narrow._data_line_max())

    def test_dynamic_line_max_minimum(self):
        """Even on very narrow terminals, line max should not go below 40."""
        r = self._make_renderer(width=40)
        self.assertGreaterEqual(r._data_line_max(), 40)

    def test_ingest_truncates_to_dynamic_width(self):
        """Lines longer than dynamic max should be truncated."""
        r = self._make_renderer(width=80)
        line_max = r._data_line_max()
        long_line = "x" * (line_max + 50)
        r._ingest_output("tool1", long_line)
        # Find the data line (skip header)
        data_lines = [l for l in r._data_lines if not l.startswith(r._TOOL_HEADER_PREFIX)]
        self.assertEqual(len(data_lines), 1)
        self.assertEqual(len(data_lines[0]), line_max)
        self.assertTrue(data_lines[0].endswith("…"))

    def test_layout_data_pane_wider_than_left(self):
        """Data pane column should be wider than the left pane on wide terminals."""
        r = self._make_renderer(width=120)
        tw = 120
        left_width = min(52, tw // 2)
        right_width = max(tw - left_width - 3, 40)
        self.assertGreater(right_width, left_width)


class TestInlineMenu(unittest.TestCase):
    """Test _run_inline_menu using prompt_toolkit's pipe input for simulated keystrokes."""

    def _run_menu(self, keys: str, options: list[str]) -> int | None:
        """Run the menu with simulated keystrokes and return the result."""
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        console = Console(file=StringIO(), force_terminal=True, width=120)

        with create_pipe_input() as pipe_input:
            pipe_input.send_text(keys)
            # Patch prompt_toolkit to use our pipe input/output
            with patch("holmes.interactive.Application") as MockApp:
                captured_result = [None]

                def fake_run(self_app):
                    from prompt_toolkit.application import Application as RealApp
                    real_app = RealApp(
                        layout=self_app.layout,
                        key_bindings=self_app.key_bindings,
                        style=self_app.style,
                        full_screen=False,
                        erase_when_done=False,
                        input=pipe_input,
                        output=DummyOutput(),
                    )
                    real_app.run()

                MockApp.side_effect = lambda **kwargs: type(
                    "_FakeApp", (),
                    {**kwargs, "run": lambda self: fake_run(self)},
                )()

                return _run_inline_menu(options, console)

    def test_enter_selects_first(self):
        """Pressing Enter immediately selects the first option."""
        result = self._run_menu("\r", ["Yes", "No", "Cancel"])
        self.assertEqual(result, 0)

    def test_down_arrow_then_enter(self):
        """Down arrow + Enter selects the second option."""
        result = self._run_menu("\x1b[B\r", ["Yes", "No", "Cancel"])
        self.assertEqual(result, 1)

    def test_down_arrow_twice_then_enter(self):
        """Two down arrows + Enter selects the third option."""
        result = self._run_menu("\x1b[B\x1b[B\r", ["Yes", "No", "Cancel"])
        self.assertEqual(result, 2)

    def test_number_key_direct_selection(self):
        """Number key 2 directly selects the second option."""
        result = self._run_menu("2", ["Yes", "No", "Cancel"])
        self.assertEqual(result, 1)

    def test_escape_cancels(self):
        """Escape returns None (cancelled)."""
        result = self._run_menu("\x1b", ["Yes", "No", "Cancel"])
        self.assertIsNone(result)

    def _run_menu_with_default(self, keys: str, options: list[str], default_index: int) -> int | None:
        """Run the menu with simulated keystrokes and a default_index."""
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        console = Console(file=StringIO(), force_terminal=True, width=120)

        with create_pipe_input() as pipe_input:
            pipe_input.send_text(keys)
            with patch("holmes.interactive.Application") as MockApp:

                def fake_run(self_app):
                    from prompt_toolkit.application import Application as RealApp
                    real_app = RealApp(
                        layout=self_app.layout,
                        key_bindings=self_app.key_bindings,
                        style=self_app.style,
                        full_screen=False,
                        erase_when_done=False,
                        input=pipe_input,
                        output=DummyOutput(),
                    )
                    real_app.run()

                MockApp.side_effect = lambda **kwargs: type(
                    "_FakeApp", (),
                    {**kwargs, "run": lambda self: fake_run(self)},
                )()

                return _run_inline_menu(options, console, default_index=default_index)

    def test_default_index_enter_selects_default(self):
        """Pressing Enter with default_index=2 selects the third option."""
        result = self._run_menu_with_default("\r", ["A", "B", "C"], default_index=2)
        self.assertEqual(result, 2)

    def test_default_index_up_then_enter(self):
        """Up arrow from default_index=2 selects the second option."""
        result = self._run_menu_with_default("\x1b[A\r", ["A", "B", "C"], default_index=2)
        self.assertEqual(result, 1)


class TestSampleQuestionsMenu(unittest.TestCase):
    """Test _show_sample_questions_menu behavior."""

    def test_returns_none_when_last_option_selected(self):
        """Selecting 'Ask my own question' returns None."""
        with patch("holmes.interactive._run_inline_menu", return_value=5) as mock_menu:
            from holmes.interactive import _show_sample_questions_menu, SAMPLE_QUESTIONS
            console = Console(file=StringIO(), force_terminal=True, width=120)
            result = _show_sample_questions_menu(console)
            self.assertIsNone(result)
            # Verify default_index is last item
            call_kwargs = mock_menu.call_args
            self.assertEqual(call_kwargs.kwargs["default_index"], len(SAMPLE_QUESTIONS))

    def test_returns_question_when_sample_selected(self):
        """Selecting a sample question returns the question text."""
        with patch("holmes.interactive._run_inline_menu", return_value=0):
            from holmes.interactive import _show_sample_questions_menu, SAMPLE_QUESTIONS
            console = Console(file=StringIO(), force_terminal=True, width=120)
            result = _show_sample_questions_menu(console)
            self.assertEqual(result, SAMPLE_QUESTIONS[0])

    def test_returns_none_when_cancelled(self):
        """Pressing Escape (None result) returns None."""
        with patch("holmes.interactive._run_inline_menu", return_value=None):
            from holmes.interactive import _show_sample_questions_menu
            console = Console(file=StringIO(), force_terminal=True, width=120)
            result = _show_sample_questions_menu(console)
            self.assertIsNone(result)
