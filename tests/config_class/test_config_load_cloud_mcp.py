"""Tests for loading cloud provider MCP server configurations.

Validates that AWS, Azure, and GCP MCP configs are correctly parsed
from YAML and produce valid RemoteMCPToolset instances, matching the
real loading path through ToolsetManager.
"""

import os
from typing import Any, Dict

import yaml

from holmes.core.tools import ToolsetType
from holmes.plugins.toolsets import load_toolsets_from_config
from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, MCPMode, RemoteMCPToolset


def _prepare_mcp_servers(mcp_servers: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Simulate ToolsetManager preprocessing: set type=mcp and enabled=true.

    In the real codebase, ToolsetManager and load_toolsets_from_file inject
    ``type: mcp`` and ``enabled: true`` before calling load_toolsets_from_config.
    This helper replicates that so our tests exercise the same code path.
    """
    for server_config in mcp_servers.values():
        server_config["type"] = ToolsetType.MCP.value
        server_config.setdefault("enabled", True)
    return mcp_servers


# --- AWS MCP config (uses legacy top-level url format) ---

aws_mcp_config_str = """
  aws_api:
    url: "http://localhost:8000"
    description: "AWS API MCP Server - comprehensive AWS service access"
    llm_instructions: "Use this server to investigate AWS infrastructure issues."
"""


def test_load_aws_mcp_config():
    """AWS MCP config with top-level url (legacy format) loads correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(aws_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert toolset.name == "aws_api"
    assert toolset.description == "AWS API MCP Server - comprehensive AWS service access"
    assert toolset.llm_instructions == "Use this server to investigate AWS infrastructure issues."


# --- Azure MCP config (uses top-level url format) ---

azure_mcp_config_str = """
  azure_api:
    url: "http://localhost:8000"
    description: "Azure API MCP Server - comprehensive Azure service access via Azure CLI"
    llm_instructions: "Use this server to investigate Azure infrastructure issues."
"""


def test_load_azure_mcp_config():
    """Azure MCP config loads correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(azure_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert toolset.name == "azure_api"
    assert toolset.description == "Azure API MCP Server - comprehensive Azure service access via Azure CLI"


# --- GCP MCP config (uses config: section with mode: sse, three servers) ---

gcp_mcp_config_str = """
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      url: "http://localhost:8000/sse"
      mode: "sse"
    llm_instructions: "Use for general GCP resource management."
  gcp_observability:
    description: "GCP Observability - logs, metrics, traces"
    config:
      url: "http://localhost:8001/sse"
      mode: "sse"
    llm_instructions: "Use for Cloud Logging, Monitoring, Trace."
  gcp_storage:
    description: "Google Cloud Storage operations"
    config:
      url: "http://localhost:8002/sse"
      mode: "sse"
    llm_instructions: "Use for investigating Cloud Storage issues."
"""


def test_load_gcp_mcp_config_multiple_servers():
    """GCP MCP config with three separate servers loads correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(gcp_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 3

    names = {t.name for t in definitions}
    assert names == {"gcp_gcloud", "gcp_observability", "gcp_storage"}

    for toolset in definitions:
        assert isinstance(toolset, RemoteMCPToolset)


def test_gcp_mcp_config_sse_mode():
    """GCP MCP servers are configured with SSE mode.

    Note: _mcp_config is populated lazily during prerequisites_callable
    (which connects to the actual server). We verify the mode via the raw
    config dict and by constructing MCPConfig directly.
    """
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(gcp_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    for toolset in definitions:
        assert isinstance(toolset, RemoteMCPToolset)
        assert toolset.config["mode"] == "sse"
        parsed = MCPConfig(**toolset.config)
        assert parsed.mode == MCPMode.SSE


def test_gcp_mcp_config_urls():
    """Each GCP MCP server has a distinct URL with the correct port."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(gcp_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    url_map = {t.name: t.config["url"] for t in definitions}
    assert "localhost:8000" in url_map["gcp_gcloud"]
    assert "localhost:8001" in url_map["gcp_observability"]
    assert "localhost:8002" in url_map["gcp_storage"]


# --- Combined multi-provider config ---

multi_provider_config_str = """
  aws_api:
    url: "http://localhost:8000"
    description: "AWS API MCP Server"
    llm_instructions: "Use for AWS infrastructure."
  azure_api:
    url: "http://localhost:8001"
    description: "Azure API MCP Server"
    llm_instructions: "Use for Azure infrastructure."
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      url: "http://localhost:8002/sse"
      mode: "sse"
    llm_instructions: "Use for GCP infrastructure."
"""


def test_load_multi_provider_config():
    """Multiple cloud provider MCP servers can be configured together."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(multi_provider_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 3
    names = {t.name for t in definitions}
    assert names == {"aws_api", "azure_api", "gcp_gcloud"}

    for toolset in definitions:
        assert isinstance(toolset, RemoteMCPToolset)
        assert toolset.llm_instructions is not None


# --- Config with headers and env var substitution ---

config_with_headers_str = """
  cloud_api:
    description: "Cloud API with auth headers"
    config:
      url: "http://localhost:8000/mcp/messages"
      mode: "streamable-http"
      headers:
        Authorization: "Bearer {{ env.CLOUD_API_KEY }}"
        X-Custom-Header: "static-value"
"""


def test_mcp_config_with_env_var_headers():
    """MCP config with environment variable substitution in headers.

    Environment variables in headers are resolved during config loading
    (via replace_env_vars_values), so the resolved values end up in the
    raw config dict.
    """
    original_env = os.environ.copy()
    try:
        os.environ["CLOUD_API_KEY"] = "test-secret-key-123"

        mcp_servers = _prepare_mcp_servers(yaml.safe_load(config_with_headers_str))
        definitions = load_toolsets_from_config(
            toolsets=mcp_servers, strict_check=False
        )

        assert len(definitions) == 1
        toolset = definitions[0]
        assert isinstance(toolset, RemoteMCPToolset)
        assert toolset.config is not None
        assert toolset.config["mode"] == "streamable-http"
        assert toolset.config["headers"]["Authorization"] == "Bearer test-secret-key-123"
        assert toolset.config["headers"]["X-Custom-Header"] == "static-value"
        # Verify MCPConfig can parse the resolved config
        parsed = MCPConfig(**toolset.config)
        assert parsed.mode == MCPMode.STREAMABLE_HTTP
        assert parsed.headers["Authorization"] == "Bearer test-secret-key-123"
    finally:
        os.environ.clear()
        os.environ.update(original_env)


# --- Config with multiline llm_instructions ---

config_with_instructions_str = """
  aws_api:
    url: "http://localhost:8000"
    description: "AWS API MCP Server"
    llm_instructions: |
      IMPORTANT: When investigating AWS issues, always:
      1. Gather current resource state
      2. Check CloudTrail for recent changes
      3. Collect CloudWatch metrics
"""


def test_mcp_config_preserves_multiline_llm_instructions():
    """Multi-line llm_instructions are preserved in the loaded config."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(config_with_instructions_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert "CloudTrail" in toolset.llm_instructions
    assert "CloudWatch" in toolset.llm_instructions
    assert "1. Gather current resource state" in toolset.llm_instructions
