"""Config-path precedence in a fresh CLI process (defaults are read at import)."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

CLI_PROBE = """
import json
import sys
from unittest.mock import patch

from typer.testing import CliRunner

from holmes.config import Config
from holmes.core.config import config_path_dir
from holmes.main import app


class ConfigLoaded(Exception):
    pass


load_config = Config.load_from_file
seen = {}


def capture_config(path, **kwargs):
    config = load_config(path, **kwargs)
    seen.update(path=str(path), max_steps=config.max_steps, state_dir=config_path_dir)
    # Stop before toolset discovery, which may contact external services.
    raise ConfigLoaded()


with (
    patch.object(Config, "load_from_file", side_effect=capture_config),
    patch.object(Config, "log_useful_info"),
):
    result = CliRunner().invoke(app, ["toolset", "list", *sys.argv[1:]])
assert isinstance(result.exception, ConfigLoaded), result.output
print("CONFIG_RESULT=" + json.dumps(seen))
"""


@pytest.mark.parametrize(
    "file_override, explicit_config, expected_steps",
    [
        (None, False, 7),
        ("", False, 7),
        ("absolute", False, 13),
        ("relative", False, 13),
        ("absolute", True, 19),
        (None, True, 19),
    ],
)
def test_cli_config_path_precedence(
    tmp_path, file_override, explicit_config, expected_steps
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "config.yaml").write_text("max_steps: 7\n")
    custom_file = tmp_path / "custom.yaml"
    custom_file.write_text("max_steps: 13\n")
    explicit_file = tmp_path / "explicit.yaml"
    explicit_file.write_text("max_steps: 19\n")

    env = os.environ.copy()
    env["HOLMES_CONFIGPATH_DIR"] = str(state_dir)
    env.pop("HOLMES_CONFIG_PATH", None)
    if file_override is not None:
        env["HOLMES_CONFIG_PATH"] = {
            "": "",
            "absolute": str(custom_file),
            "relative": os.path.relpath(custom_file, REPO_ROOT),
        }[file_override]
    args = ["--config", str(explicit_file)] if explicit_config else []

    result = subprocess.run(
        [sys.executable, "-c", CLI_PROBE, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith("CONFIG_RESULT=")
    )
    actual = json.loads(line.removeprefix("CONFIG_RESULT="))
    assert actual["max_steps"] == expected_steps
    if explicit_config:
        expected_path = explicit_file
    elif file_override:
        expected_path = custom_file
    else:
        expected_path = state_dir / "config.yaml"
    assert (REPO_ROOT / Path(actual["path"])).resolve() == expected_path
    assert actual["state_dir"] == str(state_dir)
