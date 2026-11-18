import logging
import os
import re
from typing import Any, Optional

from pydantic import SecretStr


def environ_get_safe_int(env_var: str, default: str = "0") -> int:
    try:
        return max(int(os.environ.get(env_var, default)), 0)
    except ValueError:
        return int(default)


ENV_PLACEHOLDER_PATTERN = re.compile(r"{{\s*env\.([^}]*)\s*}}")


def find_unset_env_vars(value: Any) -> list[str]:
    """`{{ env.X }}` names in `value` whose variable is unset or empty."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if isinstance(value, str):
        names = (name.strip() for name in ENV_PLACEHOLDER_PATTERN.findall(value))
        return sorted({name for name in names if not os.environ.get(name)})
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return sorted({name for item in value for name in find_unset_env_vars(item)})
    return []


def get_env_replacement(value: str) -> Optional[str]:
    env_patterns = ENV_PLACEHOLDER_PATTERN.findall(value)

    result = value

    # Replace env patterns with their values or raise exception
    for env_var_key in env_patterns:
        env_var_key = env_var_key.strip()
        pattern_regex = r"{{\s*env\." + re.escape(env_var_key) + r"\s*}}"
        if env_var_key in os.environ:
            replacement = os.environ[env_var_key]
        else:
            msg = f"ENV var replacement {env_var_key} does not exist"
            logging.error(msg)
            raise ValueError(msg)
        # A callable replacement keeps backslashes in the value literal.
        result = re.sub(pattern_regex, lambda _, value=replacement: value, result)

    return result


def replace_env_vars_values(values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if isinstance(value, str):
            env_var_value = get_env_replacement(value)
            if env_var_value is not None:
                values[key] = env_var_value
        elif isinstance(value, SecretStr):
            env_var_value = get_env_replacement(value.get_secret_value())
            if env_var_value is not None:
                values[key] = SecretStr(env_var_value)
        elif isinstance(value, dict):
            replace_env_vars_values(value)
        elif isinstance(value, list):
            # can be a list of strings
            values[key] = [
                (
                    replace_env_vars_values(item)
                    if isinstance(item, dict)
                    else get_env_replacement(item)
                    if isinstance(item, str)
                    else item
                )
                for item in value
            ]
    return values
