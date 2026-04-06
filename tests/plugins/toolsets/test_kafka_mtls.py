"""
mTLS integration tests for the kafka/admin toolset.

Requires a Kafka cluster reachable with mTLS.  All configuration comes from
environment variables so the test is safe to run in CI (it simply skips when
the env vars are absent).

Environment variables
---------------------
KAFKA_BOOTSTRAP_SERVER
    Comma-separated list of broker addresses, e.g.
    ``kafkabroker01.cec.lab.emc.com:9093,...``

KAFKA_TLS_CA_CERT_FILE
    Path to the CA certificate bundle (PEM).

KAFKA_TLS_CERT_FILE
    Path to the client certificate (PEM) for mTLS.

KAFKA_TLS_KEY_FILE
    Path to the client private key (PEM) for mTLS.

Quick-start helper
------------------
Run ``python tests/plugins/toolsets/test_kafka_mtls.py --extract-certs`` to
extract the cert files from the robusta-manifest Kubernetes secret YAML into
``/tmp/kafka-tls/``, then export the env vars and re-run pytest normally.
"""

import os
import re
import sys
import tempfile
import textwrap

import pytest

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.kafka import (
    DescribeTopic,
    FindConsumerGroupsByTopic,
    KafkaToolset,
    ListKafkaConsumers,
    ListTopics,
)
from tests.conftest import create_mock_tool_invoke_context

# ---------------------------------------------------------------------------
# Skip the whole module if the required env vars are not set
# ---------------------------------------------------------------------------

_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVER", "")
_CA = os.environ.get("KAFKA_TLS_CA_CERT_FILE", "")
_CERT = os.environ.get("KAFKA_TLS_CERT_FILE", "")
_KEY = os.environ.get("KAFKA_TLS_KEY_FILE", "")

_SKIP_REASON = (
    "mTLS Kafka env vars not set — export KAFKA_BOOTSTRAP_SERVER, "
    "KAFKA_TLS_CA_CERT_FILE, KAFKA_TLS_CERT_FILE, KAFKA_TLS_KEY_FILE"
)

if not (_BROKER and _CA and _CERT and _KEY):
    pytestmark = pytest.mark.skip(reason=_SKIP_REASON)

CLUSTER_NAME = "dell-kafka"


@pytest.fixture(scope="module")
def mtls_toolset():
    """Create a KafkaToolset configured for mTLS against the Dell Kafka cluster."""
    kafka_config = {
        "clusters": [
            {
                "name": CLUSTER_NAME,
                "broker": _BROKER,
                "ssl_ca_cert_path": _CA,
                "ssl_client_cert_path": _CERT,
                "ssl_client_key_path": _KEY,
                # security.protocol is auto-set to SSL by _build_ssl_config
            }
        ]
    }
    toolset = KafkaToolset()
    toolset.config = kafka_config
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED, (
        f"mTLS Kafka toolset failed to initialize: {toolset.error}\n"
        f"  broker={_BROKER}\n"
        f"  ca={_CA}\n"
        f"  cert={_CERT}\n"
        f"  key={_KEY}"
    )
    # Verify SSL config was picked up
    assert CLUSTER_NAME in toolset.ssl_configs, "ssl_configs not populated"
    assert toolset.ssl_configs[CLUSTER_NAME].get("ssl.ca.location") == _CA
    assert toolset.ssl_configs[CLUSTER_NAME].get("ssl.certificate.location") == _CERT
    assert toolset.ssl_configs[CLUSTER_NAME].get("ssl.key.location") == _KEY
    assert toolset.ssl_configs[CLUSTER_NAME].get("security.protocol") == "SSL"
    return toolset


def test_mtls_list_topics(mtls_toolset):
    """List topics from the mTLS-protected Kafka cluster."""
    tool = ListTopics(mtls_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"kafka_cluster_name": CLUSTER_NAME}, context)
    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "topics" in result.data, f"Unexpected response: {result.data}"


def test_mtls_list_consumers(mtls_toolset):
    """List consumer groups from the mTLS-protected Kafka cluster."""
    tool = ListKafkaConsumers(mtls_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke({"kafka_cluster_name": CLUSTER_NAME}, context)
    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    # Either consumer groups found (YAML) or empty list
    assert "consumer_groups:" in result.data, (
        f"Unexpected response: {result.data}"
    )


def test_mtls_describe_nonexistent_topic(mtls_toolset):
    """Describe a topic that does not exist — should return a clean error, not crash."""
    tool = DescribeTopic(mtls_toolset)
    context = create_mock_tool_invoke_context()
    result = tool.invoke(
        {
            "kafka_cluster_name": CLUSTER_NAME,
            "topic_name": "holmes-mtls-test-nonexistent-topic",
        },
        context,
    )
    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS
    metadata = result.data.get("metadata", {})
    assert metadata.get("topic") == "holmes-mtls-test-nonexistent-topic"


def test_mtls_find_consumer_groups_by_topic(mtls_toolset):
    """Call FindConsumerGroupsByTopic — exercises the mTLS Consumer path."""
    tool = FindConsumerGroupsByTopic(mtls_toolset)
    context = create_mock_tool_invoke_context()
    # Use a topic that almost certainly doesn't exist; expect a clean message
    result = tool.invoke(
        {
            "kafka_cluster_name": CLUSTER_NAME,
            "topic_name": "holmes-mtls-test-nonexistent-topic",
        },
        context,
    )
    assert isinstance(result, StructuredToolResult)
    assert result.status == StructuredToolResultStatus.SUCCESS


# ---------------------------------------------------------------------------
# CLI helper: extract certs from robusta-manifest secret to /tmp/kafka-tls/
# ---------------------------------------------------------------------------

# WORKSPACE_ROOT points to the repo root (three levels up from tests/plugins/toolsets/).
# ROBUSTA_SECRET defaults to the kafka-tls-credentials.yaml inside a robusta-manifest
# repo sitting *beside* (sibling to) the holmesgpt checkout — a layout used by internal
# contributors.  External contributors can override the path via the env var:
#   export ROBUSTA_MANIFEST_SECRET=/path/to/kafka-tls-credentials.yaml
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
ROBUSTA_SECRET = os.environ.get(
    "ROBUSTA_MANIFEST_SECRET",
    os.path.join(
        WORKSPACE_ROOT,
        "..",  # robusta-manifest sits beside holmesgpt/
        "robusta-manifest/secrets/robusta-agent/kafka-tls-credentials.yaml",
    ),
)


def _extract_certs(dest_dir: str | None = None) -> None:
    """
    LOCAL DEVELOPMENT HELPER — not for CI or production use.

    Parse the robusta-manifest kafka-tls-credentials.yaml and write cert files
    to *dest_dir*.  Prints export commands ready to paste into a shell.

    If *dest_dir* is not provided a unique secure temporary directory is created
    with ``tempfile.mkdtemp`` so each run uses an isolated, non-predictable path.

    The secret file is read from *ROBUSTA_SECRET* (module-level), which can be
    overridden by setting the ``ROBUSTA_MANIFEST_SECRET`` environment variable
    before running the helper::

        export ROBUSTA_MANIFEST_SECRET=/path/to/kafka-tls-credentials.yaml

    Hardcoded / assumed values:
      - broker list: five Dell-internal hosts on port 9093
        (kafkabroker01-05.cec.lab.emc.com)
      - secret source: ROBUSTA_SECRET path pointing to
        ``robusta-manifest/secrets/robusta-agent/kafka-tls-credentials.yaml``
        relative to the workspace root
    Override KAFKA_BOOTSTRAP_SERVER, KAFKA_TLS_CA_CERT_FILE,
    KAFKA_TLS_CERT_FILE, and KAFKA_TLS_KEY_FILE before running the tests if
    your environment differs.
    """
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="kafka-tls-")

    secret_path = ROBUSTA_SECRET
    if not os.path.isfile(secret_path):
        print(f"ERROR: secret file not found: {secret_path}", file=sys.stderr)
        sys.exit(1)

    with open(secret_path) as f:
        content = f.read()

    # Simple regex-based extraction (avoids yaml lib dependency on the secret)
    pattern = re.compile(
        r"^\s{2}(kafka_[^:]+):\s*\|\n((?:(?:    [^\n]*)?\n)+)",
        re.MULTILINE,
    )

    files = {}
    for match in pattern.finditer(content):
        key = match.group(1).strip()
        value = textwrap.dedent(match.group(2))
        files[key] = value.strip()

    if not files:
        print("ERROR: no cert entries found in secret YAML", file=sys.stderr)
        sys.exit(1)

    os.makedirs(dest_dir, mode=0o700, exist_ok=True)

    key_map = {
        "kafka_dellca2018-bundle.crt": "ca.crt",
        "kafka_certificate.pem": "client.pem",
        "kafka_private_key.pem": "client.key",
    }

    written = {}
    for secret_key, filename in key_map.items():
        if secret_key not in files:
            print(f"WARNING: {secret_key} not found in secret", file=sys.stderr)
            continue
        dest = os.path.join(dest_dir, filename)
        with open(dest, "w") as f:
            f.write(files[secret_key] + "\n")
        if "key" in filename:
            os.chmod(dest, 0o600)
        written[secret_key] = dest
        print(f"Wrote {dest}")

    brokers = (
        "kafkabroker01.cec.lab.emc.com:9093,"
        "kafkabroker02.cec.lab.emc.com:9093,"
        "kafkabroker03.cec.lab.emc.com:9093,"
        "kafkabroker04.cec.lab.emc.com:9093,"
        "kafkabroker05.cec.lab.emc.com:9093"
    )

    print("\n# Paste these exports into your shell before running pytest:\n")
    print(f'export KAFKA_BOOTSTRAP_SERVER="{brokers}"')
    print(f'export KAFKA_TLS_CA_CERT_FILE="{written.get("kafka_dellca2018-bundle.crt", "")}"')
    print(f'export KAFKA_TLS_CERT_FILE="{written.get("kafka_certificate.pem", "")}"')
    print(f'export KAFKA_TLS_KEY_FILE="{written.get("kafka_private_key.pem", "")}"')
    print(
        "\n# Then run:\n"
        "# cd /home/dev/build/holmesgpt\n"
        "# poetry run pytest tests/plugins/toolsets/test_kafka_mtls.py -v"
    )


if __name__ == "__main__":
    if "--extract-certs" in sys.argv:
        _extract_certs()
    else:
        print(
            "Usage:\n"
            "  python test_kafka_mtls.py --extract-certs   # extract certs to /tmp/kafka-tls/\n"
            "  pytest test_kafka_mtls.py -v                # run tests (needs env vars)"
        )
