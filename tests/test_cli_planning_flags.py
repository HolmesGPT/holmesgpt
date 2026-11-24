"""ROB-574: `holmes ask` runs in fast mode by default; --extended-planning opts in."""

from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from holmes.core.prompt import todowrite_overrides
from holmes.main import app

runner = CliRunner()


def test_fast_mode_and_extended_planning_are_mutually_exclusive():
    result = runner.invoke(
        app, ["ask", "--fast-mode", "--extended-planning", "what is wrong?"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_enable_todos_is_an_alias_for_extended_planning():
    result = runner.invoke(
        app, ["ask", "--fast-mode", "--enable-todos", "what is wrong?"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def _ask_option(name: str):
    ask = typer.main.get_command(app).commands["ask"]
    return next(p for p in ask.params if name in getattr(p, "opts", ()))


def test_ask_declares_the_planning_flags():
    """Asserted on the Click definition rather than the rendered help: Rich
    truncates long option names with an ellipsis at narrow terminal widths."""
    extended = _ask_option("--extended-planning")
    assert "--enable-todos" in extended.opts
    assert extended.default is False
    fast = _ask_option("--fast-mode")
    assert "Deprecated" in fast.help
    assert "--extended-planning" in fast.help


class _Captured(Exception):
    """Raised from the patched prompt builder so `ask` stops right after the
    CLI has forwarded its planning flags, before any LLM call."""


def _run_ask(*flags: str):
    """Invoke `holmes ask` for real up to build_initial_ask_messages and return the
    prompt_component_overrides the CLI handed it."""
    seen: dict = {}

    def capture(*args, **kwargs):
        seen["overrides"] = kwargs.get("prompt_component_overrides")
        raise _Captured()

    config = MagicMock()
    config.model = "test-model"
    with (
        patch("holmes.main.Config.load_from_file", return_value=config),
        patch("holmes.main.enable_disk_token_store"),
        patch("holmes.main.build_initial_ask_messages", side_effect=capture),
    ):
        result = runner.invoke(app, ["ask", *flags, "what is wrong?"])
    assert isinstance(result.exception, _Captured), result.output
    return seen["overrides"]


def test_ask_default_forwards_no_overrides_so_holmes_fast_mode_applies():
    assert _run_ask() is None


def test_ask_extended_planning_forwards_explicit_todowrite_opt_in():
    assert _run_ask("--extended-planning") == todowrite_overrides(True)


def test_ask_enable_todos_alias_forwards_the_same_opt_in():
    assert _run_ask("--enable-todos") == todowrite_overrides(True)


def test_ask_deprecated_fast_mode_forwards_no_overrides():
    assert _run_ask("--fast-mode") is None
