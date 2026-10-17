import os
import sys
from typing import Optional


def external_program_env() -> Optional[dict[str, str]]:
    """Restore the Linux library path for system programs in frozen builds."""
    if not getattr(sys, "frozen", False) or sys.platform != "linux":
        return None

    env = os.environ.copy()
    original_path = env.get("LD_LIBRARY_PATH_ORIG")
    if original_path is None:
        env.pop("LD_LIBRARY_PATH", None)
    else:
        env["LD_LIBRARY_PATH"] = original_path
    return env
