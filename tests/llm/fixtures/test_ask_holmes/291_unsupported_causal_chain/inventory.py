"""Serve delayed responses without exposing the implementation in pod metadata."""

import http.server
import socketserver
import time


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(5)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", 8080), Handler) as httpd:
        httpd.serve_forever()
