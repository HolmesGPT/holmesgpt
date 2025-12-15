import socket


def is_service_running(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a service is reachable on the given host and port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_grafana_running(port: int = 3000):
    if is_service_running("localhost", port):
        return None
    return "Grafana is not running. Start it with: docker compose -f docker-compose.yaml up -d"
