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

    units = {
        "ms": 0.001,
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
        "y": 31536000,
    }

    # Check for partial time formats (e.g., 1h30m, 5m12s, 1d2h30m).
    # "ms" must come before "m" in the alternation, or "500ms" parses as 500 minutes.
    pattern = r"(\d+(?:\.\d+)?)(ms|y|w|d|h|m|s)"
    matches = re.findall(pattern, s)

    if matches:
        total_seconds = 0.0
        for value_str, unit in matches:
            value = float(value_str)
            total_seconds += value * units[unit]
        return total_seconds

    # fallback: try float seconds
    return float(s)
