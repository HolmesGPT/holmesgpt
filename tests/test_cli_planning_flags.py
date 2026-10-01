"""ROB-574: `holmes ask` runs in fast mode by default; --extended-planning opts in."""

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


def test_ask_help_documents_the_planning_flags():
    result = runner.invoke(app, ["ask", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "--extended-planning" in result.output
    assert "--fast-mode" in result.output
    assert "Deprecated" in result.output
