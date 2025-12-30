import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from holmes.plugins.toolsets.connectivity_check import http_check, tcp_check


def start_tcp_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    port = server_socket.getsockname()[1]
    stop_event = threading.Event()

    def serve():
        while not stop_event.is_set():
            try:
                server_socket.settimeout(0.1)
                conn, _ = server_socket.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server_socket, port, stop_event, thread


class OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_http_server():
    server = HTTPServer(("127.0.0.1", 0), OkHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def get_unused_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_tcp_check_success():
    server_socket, port, stop_event, thread = start_tcp_server()
    try:
        result = tcp_check("127.0.0.1", port, timeout=1)
        assert result["ok"] is True
    finally:
        stop_event.set()
        server_socket.close()
        thread.join(timeout=1)


def test_tcp_check_invalid_port():
    result = tcp_check("127.0.0.1", 70000)
    assert result["ok"] is False
    assert "invalid port" in result["error"]


def test_tcp_check_unreachable_port():
    port = get_unused_port()
    result = tcp_check("127.0.0.1", port, timeout=1)
    assert result["ok"] is False
    assert "error" in result


def test_http_check_success():
    server, port, thread = start_http_server()
    try:
        result = http_check("127.0.0.1", port, path="/", timeout=1)
        assert result["ok"] is True
        assert result["status"] == 204
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_http_check_invalid_port():
    result = http_check("127.0.0.1", 0)
    assert result["ok"] is False
    assert result["error"] == "invalid port"
