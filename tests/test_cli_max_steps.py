from pathlib import Path

import pytest
from typer.testing import CliRunner

from holmes.config import Config
from holmes.main import app


class _LLMSetupReached(Exception):
    """Stop after capturing configuration before initializing tools or an LLM."""


@pytest.mark.parametrize(
    "command,arguments",
    [(["ask"], ["what is wrong?"]), (["investigate", "alertmanager"], [])],
    ids=["ask", "investigate-alertmanager"],
)
@pytest.mark.parametrize(
    "yaml_steps,cli_steps,expected_steps",
    [(None, None, 100), (23, None, 23), (23, 7, 7)],
    ids=["built-in-default", "yaml", "cli-override"],
)
def test_cli_max_steps_precedence(
    command: list[str],
    arguments: list[str],
    yaml_steps: int | None,
    cli_steps: int | None,
    expected_steps: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.yaml"
    if yaml_steps is not None:
        config_file.write_text(f"max_steps: {yaml_steps}\n")
    flags = ["--config", str(config_file)]
    if cli_steps is not None:
        flags.extend(["--max-steps", str(cli_steps)])

    def capture_config(config: Config, **kwargs: object) -> None:
        raise _LLMSetupReached(config.max_steps)

    monkeypatch.setattr(Config, "create_toolcalling_llm", capture_config)
    monkeypatch.setattr("holmes.main.enable_disk_token_store", lambda: None)

    result = CliRunner().invoke(app, [*command, *flags, *arguments])

    assert isinstance(result.exception, _LLMSetupReached), result.output
    assert result.exception.args == (expected_steps,)
