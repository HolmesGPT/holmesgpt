"""Environment configuration support for parameterized evaluation tests.

Format: ENV_CONFIGS='config1:VAR1=val1;VAR2=val2|config2:VAR1=val3'
  - | separates configurations
  - : separates name from variables
  - ; separates variables (allows commas in values)
  - Empty vars (name:) creates a baseline with no env changes
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class EnvConfig:
    """Environment configuration with a name and env vars to set during test execution."""

    name: str
    env_vars: Dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        if self.env_vars:
            vars_str = ",".join(f"{k}={v}" for k, v in sorted(self.env_vars.items()))
            return f"EnvConfig({self.name}: {vars_str})"
        return f"EnvConfig({self.name})"


def parse_env_configs(env_configs_str: str) -> List[EnvConfig]:
    """Parse ENV_CONFIGS string into EnvConfig objects. Returns default config if empty."""
    if not env_configs_str or not env_configs_str.strip():
        return [EnvConfig("default", {})]

    configs: List[EnvConfig] = []

    for config_part in env_configs_str.strip().split("|"):
        config_part = config_part.strip()
        if not config_part:
            continue

        if ":" not in config_part:
            raise ValueError(
                f"Invalid config format: '{config_part}'. "
                "Expected 'name:VAR1=val1;VAR2=val2' or 'name:' for baseline."
            )

        name, vars_str = config_part.split(":", 1)
        name = name.strip()

        if not name:
            raise ValueError(f"Config name cannot be empty in '{config_part}'")

        env_vars: Dict[str, str] = {}
        if vars_str.strip():
            for var_pair in vars_str.strip().split(";"):
                var_pair = var_pair.strip()
                if not var_pair:
                    continue

                if "=" not in var_pair:
                    raise ValueError(
                        f"Invalid variable format: '{var_pair}' in config '{name}'. "
                        "Expected 'VAR=value'."
                    )

                var_name, var_value = var_pair.split("=", 1)
                var_name = var_name.strip()
                var_value = var_value.strip()

                if not var_name:
                    raise ValueError(f"Variable name cannot be empty in '{var_pair}'")

                env_vars[var_name] = var_value

        configs.append(EnvConfig(name=name, env_vars=env_vars))

    return configs if configs else [EnvConfig("default", {})]


def get_env_configs() -> List[EnvConfig]:
    """Get environment configurations from ENV_CONFIGS env var, or default config if not set."""
    return parse_env_configs(os.environ.get("ENV_CONFIGS", ""))
