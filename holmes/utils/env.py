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


def _env_value(match: re.Match[str]) -> str:
    env_var_key = match.group(1).strip()
    if env_var_key not in os.environ:
        msg = f"ENV var replacement {env_var_key} does not exist"
        logging.error(msg)
        raise ValueError(msg)
    return os.environ[env_var_key]


def get_env_replacement(value: str) -> Optional[str]:
    # One pass, with a callable: a value's backslashes or placeholder-like text
    # is inserted literally and never substituted again.
    return ENV_PLACEHOLDER_PATTERN.sub(_env_value, value)


def _replace_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return get_env_replacement(value)
    if isinstance(value, SecretStr):
        return SecretStr(get_env_replacement(value.get_secret_value()) or "")
    if isinstance(value, dict):
        return replace_env_vars_values(value)
    if isinstance(value, list):
        return [_replace_env_vars(item) for item in value]
    return value


def replace_env_vars_values(values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        values[key] = _replace_env_vars(value)
    return values
