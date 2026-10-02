import os
from unittest.mock import patch

from holmes.common.env_vars import _load_temperature


def test_temperature_can_be_disabled():
    # Models like Anthropic Opus 4.7+ reject the temperature param; an empty/none
    # value must omit it entirely (so completion() passes temperature=None).
    for value in ("", "none", "None", "NULL"):
        with patch.dict(os.environ, {"TEMPERATURE": value}):
            assert _load_temperature() is None


def test_temperature_explicit_value():
    with patch.dict(os.environ, {"TEMPERATURE": "0.5"}):
        assert _load_temperature() == 0.5


def test_temperature_default_when_unset():
    env = {k: v for k, v in os.environ.items() if k != "TEMPERATURE"}
    with patch.dict(os.environ, env, clear=True):
        assert _load_temperature() == 0.00000001
