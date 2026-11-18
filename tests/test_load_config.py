import os

import pytest

from pydantic import SecretStr

from holmes.utils.env import (
    find_unset_env_vars,
    get_env_replacement,
    replace_env_vars_values,
)


@pytest.mark.parametrize(
    "input_value, mock_environ, expected_output",
    [
        ("this is a plain string", {}, "this is a plain string"),
        ("{{ other_format.VAR }}", {}, "{{ other_format.VAR }}"),
        ("{{env.VAR}", {}, "{{env.VAR}"),
        ("{ env.VAR }}", {}, "{ env.VAR }}"),
        ("{{ foo.bar }}", {}, "{{ foo.bar }}"),
        ("{{ VAR }}", {}, "{{ VAR }}"),
        ("{{ env.MY_VAR }}", {"MY_VAR": "var_value_123"}, "var_value_123"),
        (
            "{{  env.MY_VAR_SPACED  }}",
            {"MY_VAR_SPACED": "spaced_value_456"},
            "spaced_value_456",
        ),
        (
            "prefix {{ env.MY_VAR }} suffix",
            {"MY_VAR": "var_value_789"},
            "prefix var_value_789 suffix",
        ),
        (
            "{{ env.FIRST_VAR }} {{ env.SECOND_VAR }}",
            {"FIRST_VAR": "first_val", "SECOND_VAR": "second_val"},
            "first_val second_val",
        ),
        ("{{ env.EMPTY_VAL_VAR }}", {"EMPTY_VAL_VAR": ""}, ""),
        (r"{{ env.BACKSLASH }}", {"BACKSLASH": r"a\1b\g<0>c\\d"}, r"a\1b\g<0>c\\d"),
        (
            "{{ env.app.config.host }}",
            {"app.config.host": "localhost.localdomain"},
            "localhost.localdomain",
        ),
        (
            "foo_{{ env.MYKEY }}_bar",
            {"MYKEY": "my_value_for_mykey"},
            "foo_my_value_for_mykey_bar",
        ),
        (
            "this is a {{ env.MYKEY }} env var",
            {"MYKEY": "special"},
            "this is a special env var",
        ),
    ],
)
def test_get_env_replacement_successful(
    input_value, mock_environ, expected_output, monkeypatch
):
    """
    Tests various scenarios where get_env_replacement should return a value (or None)
    without raising an exception.
    """
    # monkeypatch.setattr(os, 'environ', mock_environ) # This replaces the whole dict
    # A better way for os.environ is to set/unset specific keys if needed, or use clear and update
    monkeypatch.setattr(os, "environ", mock_environ.copy())  # Ensure we use a copy

    actual_output = get_env_replacement(input_value)
    assert actual_output == expected_output


@pytest.mark.parametrize(
    "input_value, mock_environ, expected_exception_type, expected_exception_message_regex",
    [
        (
            "{{ env.NON_EXISTENT_VAR }}",
            {},  # Ensure the variable is not in the environment
            Exception,
            r"ENV var replacement NON_EXISTENT_VAR does not exist",
        ),
        (
            "{{ env. }}",
            {},
            Exception,
            r"ENV var replacement  does not exist",  # Note: two spaces for empty key
        ),
    ],
)
def test_get_env_replacement_exceptions(
    input_value,
    mock_environ,
    expected_exception_type,
    expected_exception_message_regex,
    monkeypatch,
):
    """
    Tests scenarios where get_env_replacement is expected to raise an Exception
    and log an error.
    """
    monkeypatch.setattr(os, "environ", mock_environ.copy())

    with pytest.raises(expected_exception_type, match=expected_exception_message_regex):
        get_env_replacement(input_value)


def test_replace_env_vars_values_substitutes_empty_value(monkeypatch):
    monkeypatch.setenv("EMPTY_KEY", "")
    values = {
        "api_key": "{{ env.EMPTY_KEY }}",
        "secret": SecretStr("{{ env.EMPTY_KEY }}"),
        "nested": {"header": "{{ env.EMPTY_KEY }}"},
        "items": ["{{ env.EMPTY_KEY }}"],
    }

    result = replace_env_vars_values(values)

    assert result["api_key"] == ""
    assert result["secret"].get_secret_value() == ""
    assert result["nested"]["header"] == ""
    assert result["items"] == [""]


def test_replace_env_vars_values_keeps_backslashes(monkeypatch):
    monkeypatch.setenv("PASSWORD", r"p\1ss\word")

    result = replace_env_vars_values({"password": "{{ env.PASSWORD }}"})

    assert result["password"] == r"p\1ss\word"


def test_find_unset_env_vars(monkeypatch):
    monkeypatch.setenv("SET_VAR", "value")
    monkeypatch.setenv("EMPTY_VAR", "")
    monkeypatch.delenv("MISSING_VAR", raising=False)
    monkeypatch.delenv("OTHER_MISSING", raising=False)
    values = {
        "set": "{{ env.SET_VAR }}",
        "empty": "{{ env.EMPTY_VAR }}",
        "secret": SecretStr("{{ env.MISSING_VAR }}"),
        "nested": {"list": ["x {{env.OTHER_MISSING}} y", "{{ env.MISSING_VAR }}", 3]},
        "number": 7,
        "none": None,
        "plain": "no placeholder",
    }

    assert find_unset_env_vars(values) == ["EMPTY_VAR", "MISSING_VAR", "OTHER_MISSING"]
    assert find_unset_env_vars("{{ env.SET_VAR }}") == []
    assert find_unset_env_vars({}) == []
