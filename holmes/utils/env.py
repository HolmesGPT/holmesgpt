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


def get_env_replacement(value: str) -> Optional[str]:
    env_patterns = re.findall(r"{{\s*env\.([^}]*)\s*}}", value)

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
        result = re.sub(pattern_regex, replacement, result)

    return result


def replace_env_vars_values(values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if isinstance(value, str):
            env_var_value = get_env_replacement(value)
            if env_var_value:
                values[key] = env_var_value
        elif isinstance(value, SecretStr):
            env_var_value = get_env_replacement(value.get_secret_value())
            if env_var_value:
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
