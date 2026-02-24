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
    llm_instructions: |
      IMPORTANT: When investigating AWS issues, always:
      1. Gather current resource state
      2. Check CloudTrail for recent changes
      3. Collect CloudWatch metrics
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
    assert "CloudTrail" in toolset.llm_instructions


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


# --- Azure MCP config (stdio via azure-api-mcp Go binary) ---

azure_mcp_config_str = """
  azure_api:
    description: "Azure API MCP Server - comprehensive Azure service access via Azure CLI"
    config:
      mode: stdio
      command: "azure-api-mcp"
      args: ["--readonly"]
    llm_instructions: |
      IMPORTANT: When investigating Azure issues, always:
      1. Gather current state using Azure CLI commands
      2. Check Activity Log for recent changes
      3. Collect Azure Monitor metrics
"""


def test_load_azure_mcp_stdio_config():
    """Azure MCP config with stdio mode (azure-api-mcp) loads correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(azure_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert toolset.name == "azure_api"
    assert toolset.config["mode"] == "stdio"
    assert toolset.config["command"] == "azure-api-mcp"
    assert toolset.config["args"] == ["--readonly"]
    parsed = StdioMCPConfig(**toolset.config)
    assert parsed.mode == MCPMode.STDIO
    assert parsed.command == "azure-api-mcp"


def test_azure_mcp_readonly_flag():
    """Azure MCP config passes --readonly flag correctly."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(azure_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert "--readonly" in toolset.config["args"]


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
    llm_instructions: "Use for investigating AWS infrastructure."
  azure_api:
    description: "Azure API - query Azure resources"
    config:
      mode: stdio
      command: "azure-api-mcp"
      args: ["--readonly"]
    llm_instructions: "Use for investigating Azure infrastructure."
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

    # AWS and Azure have llm_instructions, GCP does not
    by_name = {t.name: t for t in definitions}
    assert by_name["aws_api"].llm_instructions is not None
    assert by_name["azure_api"].llm_instructions is not None
    assert by_name["gcp_gcloud"].llm_instructions is None


def test_multi_provider_different_commands():
    """Each provider uses its own command: uvx for AWS, azure-api-mcp for Azure, npx for GCP."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(multi_provider_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    commands = {t.name: t.config["command"] for t in definitions}
    assert commands["aws_api"] == "uvx"
    assert commands["azure_api"] == "azure-api-mcp"
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


# --- Multiline llm_instructions preservation ---


def test_mcp_config_preserves_multiline_llm_instructions():
    """Multi-line llm_instructions are preserved in the loaded config."""
    mcp_servers = _prepare_mcp_servers(yaml.safe_load(aws_mcp_config_str))
    definitions = load_toolsets_from_config(toolsets=mcp_servers, strict_check=False)

    assert len(definitions) == 1
    toolset = definitions[0]
    assert isinstance(toolset, RemoteMCPToolset)
    assert "CloudTrail" in toolset.llm_instructions
    assert "CloudWatch" in toolset.llm_instructions
    assert "1. Gather current resource state" in toolset.llm_instructions

