import re
from typing import Optional, Union


def parse_duration_to_seconds(v: Optional[Union[str, float, int]]) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = v.strip().lower()
    if s.isdigit():
        return float(int(s))

    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    # Check for partial time formats (e.g., 1h30m, 5m12s, 1d2h30m)
    pattern = r"(\d+(?:\.\d+)?)(d|h|m|s)"
    matches = re.findall(pattern, s)

    if matches:
        total_seconds = 0.0
        for value_str, unit in matches:
            value = float(value_str)
            total_seconds += value * units[unit]
        return float(int(total_seconds))

    # fallback: try float seconds
    return float(s)
