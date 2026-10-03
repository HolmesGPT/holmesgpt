import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from holmes.core.tools import (
    CallablePrerequisite,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
    ToolsetType,
    ToolsetYamlFromConfig,
    YAMLToolset,
)
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.toolsets import load_toolsets_from_config


@pytest.fixture
def toolset_manager():
    return ToolsetManager()


def test_cli_tool_tags(toolset_manager):
    tags = toolset_manager.cli_tool_tags
    assert ToolsetTag.CORE in tags
    assert ToolsetTag.CLI in tags


def test_server_tool_tags(toolset_manager):
    tags = toolset_manager.server_tool_tags
    assert ToolsetTag.CORE in tags
    assert ToolsetTag.CLUSTER in tags


@patch("holmes.core.toolset_manager.load_builtin_toolsets")
@patch("holmes.core.toolset_manager.load_toolsets_from_config")
def test__list_all_toolsets_merges_configs(
    mock_load_toolsets_from_config, mock_load_builtin_toolsets, toolset_manager
):
    builtin_toolset = MagicMock(spec=Toolset)
    builtin_toolset.name = "builtin"
    builtin_toolset.tags = [ToolsetTag.CORE]
    builtin_toolset.check_prerequisites = MagicMock()
    mock_load_builtin_toolsets.return_value = [builtin_toolset]
    config_toolset = MagicMock(spec=Toolset)
    config_toolset.name = "config"
    config_toolset.tags = [ToolsetTag.CLI]
    config_toolset.check_prerequisites = MagicMock()
    mock_load_toolsets_from_config.return_value = [config_toolset]

    toolset_manager.toolsets = {"config": {"description": "test config toolset"}}
    toolsets = toolset_manager._list_all_toolsets(check_prerequisites=False)
    names = [t.name for t in toolsets]
    assert "builtin" in names
    assert "config" in names


@patch("holmes.core.toolset_manager.load_builtin_toolsets")
def test__list_all_toolsets_override_builtin_config(
    mock_load_builtin_toolsets, toolset_manager
):
    builtin_toolset = YAMLToolset(
        name="builtin",
        tags=[ToolsetTag.CORE],
        description="Builtin toolset",
        experimental=False,
    )
    mock_load_builtin_toolsets.return_value = [builtin_toolset]
    toolset_manager.toolsets = {"builtin": {"enabled": False}}
    toolsets = toolset_manager._list_all_toolsets(check_prerequisites=False)
    assert len(toolsets) == 1
    assert toolsets[0].enabled is False


@patch("holmes.core.toolset_manager.load_builtin_toolsets")
def test__list_all_toolsets_custom_toolset(mock_load_builtin_toolsets, toolset_manager):
    builtin_toolset = YAMLToolset(
        name="builtin",
        tags=[ToolsetTag.CORE],
        description="Builtin toolset",
        experimental=False,
    )
    mock_load_builtin_toolsets.return_value = [builtin_toolset]
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmpfile:
        data = {"toolsets": {"builtin": {"enabled": False}}}
        json.dump(data, tmpfile, indent=2)
        tmpfile_path = tmpfile.name
    toolset_manager.custom_toolsets = [tmpfile_path]
    toolsets = toolset_manager._list_all_toolsets(check_prerequisites=False)
    assert len(toolsets) == 1
    assert toolsets[0].enabled is False
    os.remove(tmpfile_path)


@patch("holmes.core.toolset_manager.ToolsetManager._list_all_toolsets")
def test_refresh_toolset_status_creates_file(mock_list_all_toolsets, toolset_manager):
    toolset = MagicMock(spec=Toolset)
    toolset.name = "test"
    toolset.status = ToolsetStatusEnum.ENABLED
    toolset.enabled = True
    toolset.type = ToolsetType.BUILTIN
    toolset.path = None
    toolset.error = None
    toolset.model_dump_json.return_value = json.dumps(
        {
            "name": "test",
            "status": "ENABLED",
            "enabled": True,
            "type": "BUILTIN",
            "path": None,
            "error": None,
        }
    )
    mock_list_all_toolsets.return_value = [toolset]
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = os.path.join(tmpdir, "toolsets_status.json")
        toolset_manager.toolset_status_location = cache_path
        toolset_manager.refresh_toolset_status()
        assert os.path.exists(cache_path)
        with open(cache_path) as f:
            data = json.load(f)
            assert data[0]["name"] == "test"


@patch("holmes.core.toolset_manager.ToolsetManager._list_all_toolsets")
def test_load_toolset_with_status_reads_cache(mock_list_all_toolsets, toolset_manager):
    toolset = MagicMock(spec=Toolset)
    toolset.name = "test"
    toolset.tags = [ToolsetTag.CORE]
    toolset.enabled = True
    toolset.status = ToolsetStatusEnum.ENABLED
    toolset.type = ToolsetType.BUILTIN
    toolset.path = None
    toolset.error = None
    mock_list_all_toolsets.return_value = [toolset]
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = os.path.join(tmpdir, "toolsets_status.json")
        cache_data = [
            {
                "name": "test",
                "status": "enabled",
                "enabled": True,
                "type": "built-in",
                "path": None,
                "error": None,
            }
        ]
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        toolset_manager.toolset_status_location = cache_path
        result = toolset_manager.load_toolset_with_status()
        assert result[0].name == "test"
        assert result[0].enabled is True


@patch("holmes.core.toolset_manager.ToolsetManager.load_toolset_with_status")
def test_list_console_toolsets(mock_load_toolset_with_status, toolset_manager):
    toolset = MagicMock(spec=Toolset)
    toolset.tags = [ToolsetTag.CORE, ToolsetTag.CLI]
    toolset.enabled = True
    mock_load_toolset_with_status.return_value = [toolset]
    result = toolset_manager.list_console_toolsets()
    assert toolset in result


@patch("holmes.core.toolset_manager.ToolsetManager._list_all_toolsets")
def test_list_server_toolsets(mock_list_all_toolsets, toolset_manager):
    toolset = MagicMock(spec=Toolset)
    toolset.tags = [ToolsetTag.CORE, ToolsetTag.CLUSTER]
    toolset.enabled = True
    mock_list_all_toolsets.return_value = [toolset]
    result = toolset_manager.list_server_toolsets()
    assert toolset in result


@patch("holmes.core.toolset_manager.load_toolsets_from_config")
def test_load_custom_toolsets_success(mock_load_toolsets_from_config, toolset_manager):
    yaml_toolset = MagicMock(spec=Toolset)
    yaml_toolset.name = "custom"
    mock_load_toolsets_from_config.return_value = [yaml_toolset]
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmpfile:
        data = {"toolsets": {"custom": {"enabled": True, "config": {"key": "value"}}}}
        json.dump(data, tmpfile, indent=2)
        tmpfile_path = tmpfile.name
    toolset_manager.custom_toolsets = [tmpfile_path]
    result = toolset_manager.load_custom_toolsets(["builtin"])
    assert yaml_toolset in result
    os.remove(tmpfile_path)


@patch("holmes.core.toolset_manager.load_toolsets_from_config")
@patch("holmes.core.toolset_manager.benedict")
def test_load_custom_toolsets_no_file(
    mock_benedict, mock_load_toolsets_from_config, toolset_manager
):
    toolset_manager.custom_toolsets = ["/nonexistent/path.yaml"]
    with pytest.raises(FileNotFoundError):
        toolset_manager.load_custom_toolsets(["builtin"])


def test_load_custom_toolsets_none(toolset_manager):
    toolset_manager.custom_toolsets = None
    toolset_manager.custom_toolsets_from_cli = None
    assert toolset_manager.load_custom_toolsets(["builtin"]) == []


def test_add_or_merge_onto_toolsets_merges():
    existing = {}
    new_toolset = MagicMock(spec=Toolset)
    new_toolset.name = "merge"
    new_toolset.description = "This is a new toolset"
    existing_toolset = MagicMock(spec=Toolset)
    existing_toolset.name = "merge"
    existing_toolset.enabled = "enabled"
    existing["merge"] = existing_toolset
    new_toolset.override_with = MagicMock()
    ToolsetManager.add_or_merge_onto_toolsets(ToolsetManager, [new_toolset], existing)
    existing_toolset.override_with.assert_called_once_with(new_toolset)


def test_add_or_merge_onto_toolsets_adds():
    existing = {}
    new_toolset = MagicMock(spec=Toolset)
    new_toolset.name = "add"
    ToolsetManager.add_or_merge_onto_toolsets(ToolsetManager, [new_toolset], existing)
    assert existing["add"] == new_toolset


def test_load_custom_builtin_toolsets_valid(tmp_path, toolset_manager):
    custom_file = tmp_path / "custom_toolset.yaml"
    data = {
        "toolsets": {
            "dummy_tool": {
                "enabled": True,
            }
        }
    }
    custom_file.write_text(yaml.dump(data))

    toolset_manager.custom_toolsets = [custom_file]
    result = toolset_manager.load_custom_toolsets(builtin_toolsets_names=["dummy_tool"])

    assert isinstance(result, list)
    assert len(result) == 1
    tool = result[0]
    assert tool.name == "dummy_tool"
    assert str(getattr(tool, "path", None)) == str(custom_file)


def test_load_custom_toolsets_valid(tmp_path, toolset_manager):
    custom_file = tmp_path / "custom_toolset.yaml"
    data = {
        "toolsets": {
            "dummy_tool": {
                "enabled": True,
                "description": "dummy",
                "config": {"key": "value"},
            }
        }
    }
    custom_file.write_text(yaml.dump(data))

    toolset_manager.custom_toolsets = [custom_file]
    result = toolset_manager.load_custom_toolsets(builtin_toolsets_names=[])

    assert isinstance(result, list)
    assert len(result) == 1
    tool = result[0]
    assert tool.name == "dummy_tool"
    assert str(getattr(tool, "path", None)) == str(custom_file)


def test_load_custom_toolsets_missing_field_invalid(tmp_path, toolset_manager):
    """Toolsets whose YAML fails Pydantic validation (e.g. missing required
    `description`) now produce a visible FAILED placeholder in the list instead
    of silently disappearing. The placeholder carries the Pydantic error in its
    `error` attribute so the frontend can surface "Toolset X: <why>".
    """
    custom_file = tmp_path / "custom_toolset.yaml"
    data = {"toolsets": {"dummy_tool": {"enabled": True, "config": {"key": "value"}}}}
    custom_file.write_text(yaml.dump(data))

    toolset_manager.custom_toolsets = [custom_file]
    result = toolset_manager.load_custom_toolsets(builtin_toolsets_names=[])

    assert isinstance(result, list)
    assert len(result) == 1
    placeholder = result[0]
    assert placeholder.name == "dummy_tool"
    assert placeholder.status == ToolsetStatusEnum.FAILED
    assert placeholder.error is not None
    # The raised Pydantic error should mention the missing field (`description`)
    # so the user knows what to fix.
    assert "description" in placeholder.error


def test_load_custom_toolsets_invalid_yaml(tmp_path, toolset_manager):
    custom_file = tmp_path / "custom_toolset.yaml"
    custom_file.write_text("::::")

    toolset_manager.custom_toolsets = [custom_file]
    with pytest.raises(Exception) as e_info:
        toolset_manager.load_custom_toolsets(builtin_toolsets_names=[])
    assert "No 'toolsets' or 'mcp_servers' key found" in e_info.value.args[0]


def test_load_custom_toolsets_empty_file(tmp_path, toolset_manager):
    custom_file = tmp_path / "custom_toolset.yaml"
    custom_file.write_text("")

    toolset_manager.custom_toolsets = [custom_file]

    with pytest.raises(Exception) as e_info:
        toolset_manager.load_custom_toolsets(builtin_toolsets_names=[])
    assert "Invalid data type:" in e_info.value.args[0]


def test_mcp_servers_from_custom_toolset_config(tmp_path, toolset_manager):
    custom_file = tmp_path / "custom_toolset.yaml"
    data = {
        "mcp_servers": {
            "mcp1": {
                "url": "http://example.com:8000/sse",
                "description": "Test MCP server",
                "config": {"key": "value"},
            }
        }
    }
    custom_file.write_text(yaml.dump(data))

    toolset_manager.custom_toolsets = [custom_file]
    result = toolset_manager.load_custom_toolsets(builtin_toolsets_names=[])
    assert len(result) == 1
    assert result[0].name == "mcp1"
    assert result[0].type == ToolsetType.MCP


def test_mcp_servers_from_config(toolset_manager):
    mcp_servers = {
        "mcp1": {
            "url": "http://example.com:8000/sse",
            "description": "Test MCP server",
            "config": {"key": "value"},
        }
    }

    toolset_manager = ToolsetManager(
        toolsets=None,
        mcp_servers=mcp_servers,
        custom_toolsets=None,
        custom_toolsets_from_cli=None,
    )
    assert len(toolset_manager.toolsets) == 1
    assert "mcp1" in toolset_manager.toolsets
    assert toolset_manager.toolsets["mcp1"]["type"] == ToolsetType.MCP.value


# Tests for default fast model (class-level setter on LLMSummarizeTransformer)


def test_default_fast_model_set_on_class():
    """Test that set_default_fast_model sets the class-level default used by new instances."""
    from holmes.core.transformers.llm_summarize import LLMSummarizeTransformer

    original = LLMSummarizeTransformer._default_fast_model
    try:
        LLMSummarizeTransformer.set_default_fast_model("gpt-4o-mini")
        assert LLMSummarizeTransformer._default_fast_model == "gpt-4o-mini"
    finally:
        LLMSummarizeTransformer._default_fast_model = original


def test_per_instance_fast_model_overrides_default():
    """Test that per-instance fast_model takes precedence over class default."""
    from unittest.mock import patch as mock_patch

    from holmes.core.transformers.llm_summarize import LLMSummarizeTransformer

    original = LLMSummarizeTransformer._default_fast_model
    try:
        LLMSummarizeTransformer.set_default_fast_model("gpt-4o-mini")

        # Mock DefaultLLM to capture which model is used
        with mock_patch("holmes.core.transformers.llm_summarize.DefaultLLM") as mock_llm:
            instance = LLMSummarizeTransformer(fast_model="claude-haiku")
            # Should use per-instance fast_model, not the class default
            mock_llm.assert_called_once_with("claude-haiku", None)
    finally:
        LLMSummarizeTransformer._default_fast_model = original



@pytest.mark.parametrize(
    "name, config, expected_type",
    [
        ("my-mcp", {"type": "mcp", "url": "http://example.com:8000/sse", "description": "MCP server"}, ToolsetType.MCP),
        ("my-http", {"type": "http", "config": {"endpoints": [{"hosts": ["httpbin.org"], "auth": {"type": "none"}}]}}, ToolsetType.HTTP),
        ("my-db", {"type": "database", "config": {"connection_url": "postgresql://localhost/test"}}, ToolsetType.DATABASE),
        ("my-mongo", {"type": "mongodb", "config": {"connection_url": "mongodb://localhost/test"}}, ToolsetType.MONGODB),
    ],
)
def test_custom_toolset_has_type_set(name, config, expected_type):
    """Custom toolsets must have their type field set after loading."""
    toolsets = load_toolsets_from_config({name: config}, strict_check=False)
    assert len(toolsets) == 1
    assert toolsets[0].type == expected_type


@patch("holmes.core.toolset_manager.ToolsetManager._list_all_toolsets")
def test_load_toolset_with_status_null_type_in_cache(mock_list_all_toolsets, toolset_manager):
    """Loading cached status with type=null must preserve the toolset's resolved type."""
    toolset = MagicMock(spec=Toolset)
    toolset.name = "test-mcp"
    toolset.tags = [ToolsetTag.CORE]
    toolset.enabled = True
    toolset.status = ToolsetStatusEnum.ENABLED
    toolset.type = ToolsetType.MCP
    toolset.path = None
    toolset.error = None
    mock_list_all_toolsets.return_value = [toolset]

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = os.path.join(tmpdir, "toolsets_status.json")
        # Simulate the corrupted cache: type is explicitly null
        cache_data = [
            {
                "name": "test-mcp",
                "status": "enabled",
                "enabled": True,
                "type": None,
                "path": None,
                "error": None,
            }
        ]
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)

        toolset_manager.toolset_status_location = cache_path
        # This must NOT raise ValueError and must preserve the resolved type
        result = toolset_manager.load_toolset_with_status()
        assert result[0].type == ToolsetType.MCP



# ---- Tests for Toolset.override_with ----------------------------------------


def _builtin_like_toolset(name: str = "builtin") -> YAMLToolset:
    return YAMLToolset(
        name=name,
        description="builtin",
        tags=[ToolsetTag.CORE],
        tools=[],
    )


def test_override_with_copies_set_fields():
    target = _builtin_like_toolset()
    override = ToolsetYamlFromConfig(
        name="builtin",
        enabled=True,
        config={"foo": "bar"},
    )

    target.override_with(override)

    assert target.enabled is True
    assert target.config == {"foo": "bar"}


def test_override_with_does_not_override_name():
    target = _builtin_like_toolset(name="builtin")
    override = ToolsetYamlFromConfig(name="different", enabled=True)

    target.override_with(override)

    assert target.name == "builtin"


def test_override_with_enabled_false_propagates():
    """enabled=False must propagate — False must not be filtered as 'empty'."""
    target = _builtin_like_toolset()
    target.enabled = True
    override = ToolsetYamlFromConfig(name="builtin", enabled=False)

    target.override_with(override)

    assert target.enabled is False


def test_override_with_does_not_touch_unset_fields():
    """Fields not explicitly set on the override must leave the target's
    existing value alone."""
    target = _builtin_like_toolset()
    target.enabled = True
    target.description = "original description"
    target.config = {"kept": "value"}

    # Only 'enabled' is explicitly set on the override
    override = ToolsetYamlFromConfig(name="builtin", enabled=False)

    target.override_with(override)

    assert target.enabled is False
    assert target.description == "original description"
    assert target.config == {"kept": "value"}


def test_override_with_skips_empty_values():
    target = _builtin_like_toolset()
    target.description = "keep me"
    override = ToolsetYamlFromConfig(
        name="builtin",
        description="",  # empty — should be ignored
        config={},  # empty — should be ignored
    )

    target.override_with(override)

    assert target.description == "keep me"
    assert target.config is None


def test_override_with_preserves_env_var_resolution_from_yaml_file(
    tmp_path, monkeypatch
):
    """Regression: env-var substitution done by replace_env_vars_values
    must survive override_with. Previously the model_dump path round-tripped
    through benedict's serializer and reintroduced the original {{ env.X }}
    template strings."""
    monkeypatch.setenv("TEST_RESOLVED_VALUE", "RESOLVED_XX")

    yaml_file = tmp_path / "custom.yaml"
    yaml_file.write_text(
        "toolsets:\n"
        "  builtin:\n"
        "    enabled: true\n"
        "    config:\n"
        '      secret: "{{ env.TEST_RESOLVED_VALUE }}"\n'
        "      nested:\n"
        '        deep: "{{ env.TEST_RESOLVED_VALUE }}"\n'
    )

    manager = ToolsetManager(custom_toolsets=[yaml_file])
    custom = manager.load_custom_toolsets(["builtin"])
    assert len(custom) == 1
    override = custom[0]

    target = _builtin_like_toolset()
    target.override_with(override)

    assert target.config["secret"] == "RESOLVED_XX"
    assert target.config["nested"]["deep"] == "RESOLVED_XX"


def test_override_with_handles_toolset_with_callable_prerequisites():
    """Regression: override_with must not fail when the override carries
    CallablePrerequisite entries whose bound methods reference a toolset
    containing an unpicklable threading.Lock (e.g. MCP toolsets)."""
    target = _builtin_like_toolset()

    override = _builtin_like_toolset()
    override.config = {"k": "v"}
    override.prerequisites = [
        CallablePrerequisite(callable=override.check_prerequisites)
    ]
    # Mark as explicitly set so override_with picks them up
    override.__pydantic_fields_set__.update({"config", "prerequisites"})

    target.override_with(override)  # must not raise

    assert target.config == {"k": "v"}
    assert len(target.prerequisites) == 1


def test_override_with_full_flow_through_toolset_manager(tmp_path, monkeypatch):
    """End-to-end: a builtin toolset overridden from a custom YAML file that
    uses {{ env.X }} must end up with resolved values after the full
    ToolsetManager load."""
    monkeypatch.setenv("TEST_END_TO_END", "E2E_XX")

    yaml_file = tmp_path / "custom.yaml"
    yaml_file.write_text(
        "toolsets:\n"
        "  builtin:\n"
        "    enabled: true\n"
        "    config:\n"
        '      secret: "{{ env.TEST_END_TO_END }}"\n'
    )

    manager = ToolsetManager(custom_toolsets=[yaml_file])
    with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
        mock_load.return_value = [_builtin_like_toolset()]
        toolsets = manager._list_all_toolsets(check_prerequisites=False)

    assert len(toolsets) == 1
    assert toolsets[0].config["secret"] == "E2E_XX"


# ---- Integration tests for the Karpenter toolsets ---------------------------
#
# These exercise ``karpenter/core`` and ``karpenter/aws`` through the real
# ``ToolsetManager`` pipeline — builtin discovery, custom-YAML overlay, tag
# filtering and Jinja rendering — so a regression in any of those layers
# surfaces here even though the unit tests in
# ``tests/plugins/toolsets/test_karpenter_toolset.py`` use the raw YAML
# loader directly. Prerequisites are skipped so the tests don't depend on
# a Karpenter-enabled kubectl context.


def _write_karpenter_custom_toolsets_yaml(tmp_path):
    yaml_file = tmp_path / "karpenter_toolsets.yaml"
    yaml_file.write_text(
        "toolsets:\n"
        "  karpenter/core:\n"
        "    enabled: true\n"
        "  karpenter/aws:\n"
        "    enabled: true\n"
    )
    return yaml_file


def _load_karpenter_toolsets_via_manager(tmp_path):
    """Drive ToolsetManager end-to-end with a karpenter custom YAML overlay.

    load_python_toolsets is stubbed so that the test does not depend on
    transitive Python-toolset imports (e.g. prometrix, which pins an old
    pydantic v1 that is incompatible with Python 3.14). Karpenter is a
    YAML-only toolset, so real YAML discovery still runs end-to-end and
    karpenter.yaml is picked up by load_toolsets_from_file."""
    yaml_file = _write_karpenter_custom_toolsets_yaml(tmp_path)

    manager = ToolsetManager(custom_toolsets=[yaml_file])
    with patch(
        "holmes.plugins.toolsets.load_python_toolsets", return_value=[]
    ):
        toolsets = manager._list_all_toolsets(
            check_prerequisites=False,
            toolset_tags=[ToolsetTag.CORE, ToolsetTag.CLI],
        )
    return {ts.name: ts for ts in toolsets}


def test_karpenter_toolsets_load_via_toolset_manager(tmp_path):
    """Both ``karpenter/core`` and ``karpenter/aws`` must be discovered as
    builtins, enabled by the user's custom YAML, and survive tag filtering
    when ToolsetManager is called in CLI mode (CORE + CLI tags)."""
    by_name = _load_karpenter_toolsets_via_manager(tmp_path)

    assert "karpenter/core" in by_name, (
        "karpenter/core must be discovered as a builtin and survive tag filtering"
    )
    assert "karpenter/aws" in by_name, (
        "karpenter/aws must be discovered as a builtin and survive tag filtering"
    )
    assert by_name["karpenter/core"].enabled is True
    assert by_name["karpenter/aws"].enabled is True

    # Tag contract: karpenter/core runs anywhere kubectl runs (CORE), while
    # karpenter/aws is AWS-gated and tagged CLI — matches aks.yaml precedent.
    assert ToolsetTag.CORE in by_name["karpenter/core"].tags
    assert ToolsetTag.CLI in by_name["karpenter/aws"].tags


def test_karpenter_tool_invocations_render_via_toolset_manager(tmp_path):
    """The diagnostic commands a Karpenter investigation triggers
    (NodePool/NodeClaim inspection, disruption events, EC2NodeClass get)
    must render with realistic parameters after the toolsets come back
    from ToolsetManager — not just from the raw YAML loader."""
    by_name = _load_karpenter_toolsets_via_manager(tmp_path)
    core_tools = {t.name: t for t in by_name["karpenter/core"].tools}
    aws_tools = {t.name: t for t in by_name["karpenter/aws"].tools}

    # NodePool listing — the first step in any "why isn't anything scaling"
    # investigation.
    nodepool_list = core_tools[
        "karpenter_nodepool_list"
    ].get_parameterized_one_liner({})
    assert "get nodepools.karpenter.sh" in nodepool_list

    # NodeClaim describe with a resource name — the primary tool for
    # diagnosing a NodeClaim that is not becoming a Node.
    describe = core_tools[
        "karpenter_nodeclaim_describe"
    ].get_parameterized_one_liner({"name": "nc-integration-xyz"})
    assert "nc-integration-xyz" in describe
    assert "describe nodeclaim" in describe

    # Disruption events must be bounded via `tail -n` with a sensible
    # default so the grep doesn't blow out context on busy clusters.
    events = core_tools[
        "karpenter_disruption_events"
    ].get_parameterized_one_liner({})
    assert "--sort-by=.lastTimestamp" in events
    assert "tail -n 100" in events

    # AWS-specific EC2NodeClass fetch — only reachable once karpenter/aws
    # is loaded, used to verify subnet/AMI/security-group selectors.
    ec2 = aws_tools[
        "karpenter_ec2nodeclass_get"
    ].get_parameterized_one_liner({"name": "default"})
    assert "default" in ec2
    assert "-o yaml" in ec2
    assert "get ec2nodeclass.karpenter.k8s.aws" in ec2
