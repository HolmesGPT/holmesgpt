"""Tests for loading cloud provider MCP server configurations.

Validates that AWS, Azure, and GCP MCP configs (stdio mode, local execution)
are correctly parsed from YAML and produce valid RemoteMCPToolset instances,
matching the real loading path through ToolsetManager.
"""

import os
from typing import Any, Dict

import yaml

from holmes.core.tools import ToolsetType
from holmes.plugins.toolsets import load_toolsets_from_config
from holmes.plugins.toolsets.mcp.toolset_mcp import MCPMode, RemoteMCPToolset, StdioMCPConfig


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


# --- AWS MCP config (stdio via uvx) ---

aws_mcp_config_str = """
  aws_api:
    description: "AWS API - execute AWS CLI commands for investigating infrastructure issues"
    config:
      mode: stdio
      command: "uvx"
      args: ["awslabs.aws-api-mcp-server@latest"]
      env:
        AWS_REGION: "us-east-1"
        READ_OPERATIONS_ONLY: "true"
"""


def test_load_aws_mcp_stdio_config():
    """AWS MCP config with stdio mode (uvx) loads as RemoteMCPToolset."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(aws_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert toolset.name == "aws_api"
    assert toolset.description == "AWS API - execute AWS CLI commands for investigating infrastructure issues"


def test_aws_mcp_stdio_config_fields():
    """AWS stdio config contains the correct command, args, and env."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(aws_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    toolset = definitions[0]
    assert toolset.config["mode"] == "stdio"
    assert toolset.config["command"] == "uvx"
    assert toolset.config["args"] == ["awslabs.aws-api-mcp-server@latest"]
    assert toolset.config["env"]["AWS_REGION"] == "us-east-1"
    assert toolset.config["env"]["READ_OPERATIONS_ONLY"] == "true"
    # Verify StdioMCPConfig can parse the config dict
    parsed = StdioMCPConfig(**toolset.config)
    assert parsed.mode == MCPMode.STDIO
    assert parsed.command == "uvx"
    assert parsed.args == ["awslabs.aws-api-mcp-server@latest"]


# --- Azure MCP config (stdio via npx) ---

azure_mcp_config_str = """
  azure_api:
    description: "Azure API - query Azure resources and investigate infrastructure issues"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@azure/mcp@latest", "server", "start"]
"""


def test_load_azure_mcp_stdio_config():
    """Azure MCP config with stdio mode (npx) loads correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(azure_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert toolset.name == "azure_api"
    assert toolset.config["mode"] == "stdio"
    assert toolset.config["command"] == "npx"
    assert toolset.config["args"] == ["-y", "@azure/mcp@latest", "server", "start"]
    parsed = StdioMCPConfig(**toolset.config)
    assert parsed.mode == MCPMode.STDIO
    assert parsed.command == "npx"


def test_azure_mcp_consolidated_mode():
    """Azure MCP config with --mode consolidated flag loads correctly."""
    config_str = """
      azure_api:
        description: "Azure API - consolidated mode"
        config:
          mode: stdio
          command: "npx"
          args: ["-y", "@azure/mcp@latest", "server", "start", "--mode", "consolidated"]
    """
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert "--mode" in toolset.config["args"]
    assert "consolidated" in toolset.config["args"]


# --- GCP MCP config (stdio via npx, three servers) ---

gcp_mcp_config_str = """
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/gcloud-mcp"]
  gcp_observability:
    description: "GCP Observability - Cloud Logging, Monitoring, Trace, Error Reporting"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/observability-mcp"]
  gcp_storage:
    description: "Google Cloud Storage operations"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/storage-mcp"]
"""


def test_load_gcp_mcp_config_multiple_servers():
    """GCP MCP config with three separate stdio servers loads correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(gcp_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 3

    names = {t.name for t in definitions}
    assert names == {"gcp_gcloud", "gcp_observability", "gcp_storage"}

    for toolset in definitions:
        assert isinstance(toolset, RemoteMCPToolset)


def test_gcp_mcp_config_stdio_mode():
    """All GCP MCP servers use stdio mode with npx."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(gcp_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    for toolset in definitions:
        assert isinstance(toolset, RemoteMCPToolset)
        assert toolset.config["mode"] == "stdio"
        assert toolset.config["command"] == "npx"
        parsed = StdioMCPConfig(**toolset.config)
        assert parsed.mode == MCPMode.STDIO


def test_gcp_mcp_config_package_names():
    """Each GCP MCP server references the correct npm package."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(gcp_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    packages = {t.name: t.config["args"][-1] for t in definitions}
    assert packages["gcp_gcloud"] == "@google-cloud/gcloud-mcp"
    assert packages["gcp_observability"] == "@google-cloud/observability-mcp"
    assert packages["gcp_storage"] == "@google-cloud/storage-mcp"


# --- Combined multi-provider config ---

multi_provider_config_str = """
  aws_api:
    description: "AWS API - execute AWS CLI commands"
    config:
      mode: stdio
      command: "uvx"
      args: ["awslabs.aws-api-mcp-server@latest"]
      env:
        AWS_REGION: "us-east-1"
        READ_OPERATIONS_ONLY: "true"
  azure_api:
    description: "Azure API - query Azure resources"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@azure/mcp@latest", "server", "start"]
  gcp_gcloud:
    description: "Google Cloud management via gcloud CLI"
    config:
      mode: stdio
      command: "npx"
      args: ["-y", "@google-cloud/gcloud-mcp"]
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
        assert toolset.config["mode"] == "stdio"


def test_multi_provider_different_commands():
    """AWS uses uvx while Azure and GCP use npx."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(multi_provider_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    commands = {t.name: t.config["command"] for t in definitions}
    assert commands["aws_api"] == "uvx"
    assert commands["azure_api"] == "npx"
    assert commands["gcp_gcloud"] == "npx"


# --- Config with env var substitution ---

config_with_env_vars_str = """
  aws_api:
    description: "AWS API with profile from env"
    config:
      mode: stdio
      command: "uvx"
      args: ["awslabs.aws-api-mcp-server@latest"]
      env:
        AWS_REGION: "{{ env.AWS_REGION }}"
        AWS_API_MCP_PROFILE_NAME: "{{ env.AWS_PROFILE }}"
        READ_OPERATIONS_ONLY: "true"
"""


def test_mcp_stdio_config_with_env_var_substitution():
    """Environment variables in stdio config env section are resolved."""
    original_env = os.environ.copy()
    try:
        os.environ["AWS_REGION"] = "eu-west-1"
        os.environ["AWS_PROFILE"] = "production"

        mcp_servers = _prepare_mcp_servers(yaml.safe_load(config_with_env_vars_str))
        definitions = load_toolsets_from_config(
            toolsets=mcp_servers, strict_check=False
        )

        assert len(definitions) == 1
        toolset = definitions[0]
        assert isinstance(toolset, RemoteMCPToolset)
        assert toolset.config["env"]["AWS_REGION"] == "eu-west-1"
        assert toolset.config["env"]["AWS_API_MCP_PROFILE_NAME"] == "production"
        assert toolset.config["env"]["READ_OPERATIONS_ONLY"] == "true"
    finally:
        os.environ.clear()
        os.environ.update(original_env)

