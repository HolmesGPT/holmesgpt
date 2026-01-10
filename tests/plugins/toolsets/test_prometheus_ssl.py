"""Tests for Prometheus SSL error handling."""

import ssl
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest

from holmes.plugins.toolsets.prometheus.prometheus import (
    PrometheusConfig,
    PrometheusToolset,
)
from tests.conftest import create_mock_tool_invoke_context


def generate_self_signed_cert(cert_file: str, key_file: str) -> None:
    """Generate a self-signed certificate for testing."""
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )

    with open(key_file, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


class DummyHandler(BaseHTTPRequestHandler):
    """Handler that returns a non-Prometheus response."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not Prometheus")

    def log_message(self, format, *args):
        pass  # Suppress logging


@pytest.fixture
def https_server(responses):
    """Start a simple HTTPS server with a self-signed certificate."""
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cert_file, \
         tempfile.NamedTemporaryFile(suffix=".key", delete=False) as key_file:

        generate_self_signed_cert(cert_file.name, key_file.name)

        server = HTTPServer(("127.0.0.1", 0), DummyHandler)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(cert_file.name, key_file.name)
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)

        port = server.server_address[1]

        # Allow requests to our test server to pass through the responses mock
        responses.add_passthru(f"https://127.0.0.1:{port}")

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        yield f"https://127.0.0.1:{port}"

        server.shutdown()


class TestSSLErrorHandling:
    """Tests for SSL certificate verification error handling."""

    def test_ssl_error_with_verify_enabled(self, https_server):
        """Test that SSL errors produce clear error messages with remediation steps."""
        toolset = PrometheusToolset()
        toolset.config = PrometheusConfig(
            prometheus_url=https_server,
            verify_ssl=True,
        )

        # Get the list_prometheus_rules tool
        rules_tool = next(t for t in toolset.tools if t.name == "list_prometheus_rules")
        rules_tool.toolset = toolset

        context = create_mock_tool_invoke_context()
        result = rules_tool.invoke({}, context)

        assert result.status.value == "error"
        assert "SSL certificate verification failed" in result.error
        assert "verify_ssl: false" in result.error
        assert "prometheus/metrics" in result.error

    def test_ssl_bypassed_with_verify_disabled(self, https_server):
        """Test that SSL verification can be disabled and we get past SSL errors."""
        toolset = PrometheusToolset()
        toolset.config = PrometheusConfig(
            prometheus_url=https_server,
            verify_ssl=False,
        )

        rules_tool = next(t for t in toolset.tools if t.name == "list_prometheus_rules")
        rules_tool.toolset = toolset

        context = create_mock_tool_invoke_context()
        result = rules_tool.invoke({}, context)

        # Should fail, but NOT with SSL error - it gets past SSL but fails parsing response
        assert result.status.value == "error"
        assert "SSL certificate verification failed" not in result.error
