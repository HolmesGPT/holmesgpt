import socket
from typing import Optional


def check_service_running(
    service_name: str, port: int, host: str = "localhost", timeout: float = 2.0
) -> Optional[str]:
    """Check if a service is running and return skip reason if not."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except (socket.timeout, ConnectionRefusedError, OSError):
        return f"{service_name} is not running on {host}:{port}"
