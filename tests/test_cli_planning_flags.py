"""ROB-574: `holmes ask` runs in fast mode by default; --extended-planning opts in."""

import typer
from typer.testing import CliRunner

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
