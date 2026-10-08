"""Renders the Kubernetes MCP addon with `helm template` and checks that a CA
certificate set in mcpAddons.kubernetes.config.certificateAuthority reaches the
MCP server pod, so `certificate_authority` in serverConfig can point at it (for
example, Dex served from a private CA)."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
TEMPLATE = "templates/mcp-servers/kubernetes/deployment.yaml"
CA_PATH = "/etc/kubernetes-mcp-ca"

CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBszCCAVmgAwIBAgIUTESTONLYTESTONLYTESTONLYTESTONLYwCgYIKoZIzj0E
-----END CERTIFICATE-----
"""

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not installed")


def _render(tmp_path, ca_pem=None, server_config=None):
    args = [
        "helm", "template", "t", str(HELM_DIR), "--namespace", "holmes",
        "--show-only", TEMPLATE,
        "--set", "mcpAddons.kubernetes.enabled=true",
    ]
    if server_config is not None:
        config_file = tmp_path / "server-config.toml"
        config_file.write_text(server_config)
        args += ["--set-file", f"mcpAddons.kubernetes.config.serverConfig={config_file}"]
    if ca_pem is not None:
        ca_file = tmp_path / "ca.pem"
        ca_file.write_text(ca_pem)
        args += ["--set-file", f"mcpAddons.kubernetes.config.certificateAuthority={ca_file}"]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return {doc["kind"]: doc for doc in yaml.safe_load_all(result.stdout) if doc}


def _pod(docs):
    return docs["Deployment"]["spec"]["template"]["spec"]


def test_no_ca_renders_nothing_extra(tmp_path):
    docs = _render(tmp_path)
    assert docs["ConfigMap"]["data"] == {}
    pod = _pod(docs)
    assert "volumes" not in pod
    assert "volumeMounts" not in pod["containers"][0]


def test_ca_is_mounted_for_certificate_authority(tmp_path):
    docs = _render(
        tmp_path,
        ca_pem=CA_PEM,
        server_config=f'require_oauth = true\ncertificate_authority = "{CA_PATH}/ca.crt"\n',
    )
    config_map = docs["ConfigMap"]
    assert config_map["data"]["ca.crt"].strip() == CA_PEM.strip()

    pod = _pod(docs)
    ca_volume = next(v for v in pod["volumes"] if v["name"] == "mcp-ca")
    assert ca_volume["configMap"]["name"] == config_map["metadata"]["name"]
    assert ca_volume["configMap"]["items"] == [{"key": "ca.crt", "path": "ca.crt"}]
    mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "mcp-ca")
    assert mount["mountPath"] == CA_PATH
    # The config.toml mount is untouched alongside it.
    assert any(m["mountPath"] == "/etc/kubernetes-mcp" for m in pod["containers"][0]["volumeMounts"])


def test_ca_without_server_config_still_mounts(tmp_path):
    """The CA can come with an external configSecret, so it must not depend on serverConfig."""
    pod = _pod(_render(tmp_path, ca_pem=CA_PEM))
    assert [v["name"] for v in pod["volumes"]] == ["mcp-ca"]
    assert [m["mountPath"] for m in pod["containers"][0]["volumeMounts"]] == [CA_PATH]
