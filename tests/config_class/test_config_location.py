"""Default config selection in fresh processes, including server file loading."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

SERVER_PROBE = """
import os
from unittest.mock import MagicMock, PropertyMock, patch

from holmes.config import Config

with (
    patch.object(Config, "dal", new_callable=PropertyMock, return_value=MagicMock(enabled=False)),
    patch.object(Config, "log_useful_info"),
    patch.object(Config, "_Config__get_cluster_name", return_value="test"),
):
    import server

    assert server.config.max_steps == int(os.environ["EXPECTED_MAX_STEPS"])
"""


@pytest.mark.parametrize("load_from_env, expected_steps", [("false", 13), ("true", 19)])
def test_server_respects_config_file_override_and_env_loading(
    tmp_path, load_from_env, expected_steps
):
    (tmp_path / "config.yaml").write_text("max_steps: 7\n")
    custom_file = tmp_path / "custom.yaml"
    custom_file.write_text("max_steps: 13\n")
    env = os.environ.copy()
    env.update(
        HOLMES_CONFIGPATH_DIR=str(tmp_path),
        HOLMES_CONFIG_PATH=str(custom_file),
        LOAD_CONFIG_FROM_ENV=load_from_env,
        MAX_STEPS="19",
        EXPECTED_MAX_STEPS=str(expected_steps),
    )
    result = subprocess.run(
        [sys.executable, "-c", SERVER_PROBE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_config_location_without_overrides():
    env = os.environ.copy()
    env.pop("HOLMES_CONFIG_PATH", None)
    env.pop("HOLMES_CONFIGPATH_DIR", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "from holmes.config import DEFAULT_CONFIG_LOCATION; "
            "assert Path(DEFAULT_CONFIG_LOCATION) == Path.home() / '.holmes' / 'config.yaml'",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
