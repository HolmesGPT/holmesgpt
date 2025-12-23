import os
import socket
import threading
from contextlib import contextmanager
import logging

from holmes.common.env_vars import load_bool

_lock = threading.RLock()

ROBUSTA_PATCH_KEEPALIVE = load_bool("ROBUSTA_PATCH_KEEPALIVE", True)
ROBUSTA_KEEPALIVE_IDLE = int(os.environ.get("ROBUSTA_KEEPALIVE_IDLE", 2))
ROBUSTA_KEEPALIVE_INTVL = int(os.environ.get("ROBUSTA_KEEPALIVE_INTVL", 2))
ROBUSTA_KEEPALIVE_CNT = int(os.environ.get("ROBUSTA_KEEPALIVE_CNT", 5))


@contextmanager
def keepalive_create_connection(
    idle=ROBUSTA_KEEPALIVE_IDLE,
    intvl=ROBUSTA_KEEPALIVE_INTVL,
    cnt=ROBUSTA_KEEPALIVE_CNT,
):
    """
    Temporarily monkey-patch socket.create_connection to enable TCP keepalive
    and (on Linux) set keepalive probe timings.

    WARNING: This is process-global while active; use the lock to avoid races.
    """
    with _lock:
        orig = socket.create_connection

        def patched(address, timeout=None, source_address=None, **kwargs):
            logging.info(
                f"Creating patched connection to {address} with timeout {timeout} and source address {source_address}"
            )
            s = orig(address, timeout=timeout, source_address=source_address, **kwargs)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            # Linux-only tuning (these attrs won't exist on macOS/Windows)
            if hasattr(socket, "TCP_KEEPIDLE"):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, int(idle))
            if hasattr(socket, "TCP_KEEPINTVL"):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, int(intvl))
            if hasattr(socket, "TCP_KEEPCNT"):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, int(cnt))
            return s

        conn_function = patched if ROBUSTA_PATCH_KEEPALIVE else orig
        socket.create_connection = conn_function
        try:
            yield
        finally:
            socket.create_connection = orig
