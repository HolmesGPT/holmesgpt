import pytest

from holmes.common.env_vars import load_bool

ENV_VAR = "HOLMES_TEST_BOOL_FLAG"


@pytest.mark.parametrize(
    "value", ["true", "True", "TRUE", " true ", "1", "yes", "Yes", "on", "ON"]
)
def test_load_bool_true_values(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    assert load_bool(ENV_VAR, False) is True


@pytest.mark.parametrize(
    "value", ["false", "False", "FALSE", " false ", "0", "no", "No", "off", "OFF"]
)
def test_load_bool_false_values(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    assert load_bool(ENV_VAR, True) is False


@pytest.mark.parametrize("default", [True, False, None])
def test_load_bool_unset_returns_default(monkeypatch, default):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert load_bool(ENV_VAR, default) is default


@pytest.mark.parametrize("value", ["", "   "])
@pytest.mark.parametrize("default", [True, False, None])
def test_load_bool_blank_returns_default(monkeypatch, value, default):
    # A blank value (for example `env: - name: X, value: ""` in a manifest) means
    # "not configured", the same as the variable being unset.
    monkeypatch.setenv(ENV_VAR, value)
    assert load_bool(ENV_VAR, default) is default


@pytest.mark.parametrize("value", ["maybe", "2", "enabled", "tru"])
def test_load_bool_invalid_value_names_the_variable(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    with pytest.raises(ValueError, match=ENV_VAR):
        load_bool(ENV_VAR, False)
