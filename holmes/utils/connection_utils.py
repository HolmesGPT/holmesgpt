import logging
import socket

from holmes.common.env_vars import KEEPALIVE_CNT, KEEPALIVE_IDLE, KEEPALIVE_INTVL


def patch_socket_create_connection(
    idle: int = KEEPALIVE_IDLE,
    intvl: int = KEEPALIVE_INTVL,
    cnt: int = KEEPALIVE_CNT,
) -> None:
    orig = socket.create_connection

    def new_create_connection(address, timeout=None, source_address=None, **kwargs):
        logging.debug(
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

    logging.info("Patching socket.create_connection to force keepalive")
    socket.create_connection = new_create_connection
