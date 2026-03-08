"""Tests for holmes.toolset_config_tui module."""

import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Type
from unittest.mock import MagicMock, patch

import pytest
import yaml  # type: ignore
from pydantic import BaseModel, Field
from rich.console import Console

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    Tool,
    Toolset,
    ToolsetStatusEnum,
    ToolsetType,
)
from holmes.toolset_config_tui import (
    ConfigFieldNode,
    _extract_enum_class,
    _flatten_tree,
    _get_existing_config,
    _resolve_primitive_type,
    _select_config_class,
    build_tree_from_schema,
    run_toolset_config_tui,
    save_config_to_file,
    select_toolset,
    run_config_test,
    tree_to_dict,
)
from holmes.utils.pydantic_utils import ToolsetConfig


# ── Fixtures ──────────────────────────────────────────────────────────


class SimpleConfig(ToolsetConfig):
    api_url: str = Field(
        title="API URL",
        description="The base URL for the API",
        examples=["http://localhost:9090"],
    )
    api_key: Optional[str] = Field(
        default=None,
        title="API Key",
        description="Authentication key",
    )
    verify_ssl: bool = Field(default=True, title="Verify SSL")
    timeout: int = Field(default=30, title="Timeout")
    rate_limit: float = Field(default=1.5, title="Rate Limit")


class NestedLabelsConfig(ToolsetConfig):
    pod: str = Field(default="k8s.pod.name", title="Pod Label")
    namespace: str = Field(default="k8s.namespace.name", title="Namespace Label")


class NestedConfig(ToolsetConfig):
    api_url: str = Field(title="URL", examples=["http://grafana:3000"])
    labels: NestedLabelsConfig = Field(
        default_factory=NestedLabelsConfig, title="Labels"
    )
    additional_headers: Optional[Dict[str, str]] = Field(
        default=None, title="Headers"
    )
    tags: Optional[List[str]] = Field(default=None, title="Tags")


class DummyTool(Tool):
    name: str = "dummy_tool"
    description: str = "A dummy tool"

    def _invoke(self, _params: dict, _user_approved: bool = False) -> StructuredToolResult:
        return StructuredToolResult(status="success", data="ok")  # type: ignore

    def get_parameterized_one_liner(self, _params: Dict) -> str:
        return ""


def make_toolset(
    name: str = "test/toolset",
    config_classes: Optional[List[Type[BaseModel]]] = None,
    config: Optional[Any] = None,
) -> Toolset:
    """Create a test toolset with optional config_classes."""
    if config_classes:
        cls = type(f"{name}_TestSubclass", (Toolset,), {"config_classes": config_classes})
    else:
        cls = Toolset
    return cls(
        name=name,
        description=f"Test toolset: {name}",
        enabled=True,
        tools=[DummyTool()],
        config=config,
    )


class ConfigurableToolset(Toolset):
    config_classes: ClassVar[List[Type[BaseModel]]] = [SimpleConfig]

    def __init__(self, **kwargs: Any):
        defaults = {
            "name": "test/configurable",
            "description": "A configurable toolset",
            "enabled": True,
            "tools": [DummyTool()],
        }
        defaults.update(kwargs)
        super().__init__(**defaults)


class NestedConfigToolset(Toolset):
    config_classes: ClassVar[List[Type[BaseModel]]] = [NestedConfig]

    def __init__(self, **kwargs: Any):
        defaults = {
            "name": "test/nested",
            "description": "A toolset with nested config",
            "enabled": True,
            "tools": [DummyTool()],
        }
        defaults.update(kwargs)
        super().__init__(**defaults)


# ── _resolve_primitive_type ───────────────────────────────────────────


class TestResolvePrimitiveType:
    def test_str(self) -> None:
        assert _resolve_primitive_type(str) == "str"

    def test_int(self) -> None:
        assert _resolve_primitive_type(int) == "int"

    def test_float(self) -> None:
        assert _resolve_primitive_type(float) == "float"

    def test_bool(self) -> None:
        assert _resolve_primitive_type(bool) == "bool"

    def test_dict(self) -> None:
        assert _resolve_primitive_type(Dict[str, str]) == "dict"

    def test_list(self) -> None:
        assert _resolve_primitive_type(List[str]) == "list"

    def test_optional_str(self) -> None:
        assert _resolve_primitive_type(Optional[str]) == "str"

    def test_optional_int(self) -> None:
        assert _resolve_primitive_type(Optional[int]) == "int"

    def test_optional_dict(self) -> None:
        assert _resolve_primitive_type(Optional[Dict[str, str]]) == "dict"

    def test_none(self) -> None:
        assert _resolve_primitive_type(None) == "str"

    def test_base_model_subclass(self) -> None:
        assert _resolve_primitive_type(NestedLabelsConfig) == "model"

    def test_optional_base_model(self) -> None:
        assert _resolve_primitive_type(Optional[NestedLabelsConfig]) == "model"


# ── build_tree_from_schema ────────────────────────────────────────────


class TestBuildTreeFromSchema:
    def test_simple_config_empty_values(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        names = [n.key for n in nodes]
        assert "api_url" in names
        assert "api_key" in names
        assert "verify_ssl" in names
        assert "timeout" in names
        assert "rate_limit" in names

    def test_simple_config_with_values(self) -> None:
        values = {"api_url": "http://test:9090", "timeout": 60}
        nodes = build_tree_from_schema(SimpleConfig, values)
        url_node = next(n for n in nodes if n.key == "api_url")
        timeout_node = next(n for n in nodes if n.key == "timeout")
        assert url_node.value == "http://test:9090"
        assert timeout_node.value == 60

    def test_simple_config_defaults(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        ssl_node = next(n for n in nodes if n.key == "verify_ssl")
        timeout_node = next(n for n in nodes if n.key == "timeout")
        assert ssl_node.value is True
        assert timeout_node.value == 30

    def test_simple_config_field_types(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        types = {n.key: n.field_type for n in nodes}
        assert types["api_url"] == "str"
        assert types["api_key"] == "str"
        assert types["verify_ssl"] == "bool"
        assert types["timeout"] == "int"
        assert types["rate_limit"] == "float"

    def test_nested_config_model_field(self) -> None:
        nodes = build_tree_from_schema(NestedConfig, {})
        labels_node = next(n for n in nodes if n.key == "labels")
        assert labels_node.is_header is True
        assert labels_node.field_type == "model"
        assert len(labels_node.children) == 2  # pod, namespace

    def test_nested_config_with_values(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "labels": {"pod": "custom_pod"},
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        labels_node = next(n for n in nodes if n.key == "labels")
        pod_child = next(c for c in labels_node.children if c.key == "pod")
        assert pod_child.value == "custom_pod"

    def test_dict_field_with_values(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "additional_headers": {"Authorization": "Bearer token123"},
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        headers_node = next(n for n in nodes if n.key == "additional_headers")
        assert headers_node.is_header is True
        assert headers_node.field_type == "dict"
        assert len(headers_node.children) == 1
        assert headers_node.children[0].key == "0"
        assert headers_node.children[0].dict_key == "Authorization"
        assert headers_node.children[0].value == "Bearer token123"

    def test_list_field_with_values(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "tags": ["tag1", "tag2"],
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        tags_node = next(n for n in nodes if n.key == "tags")
        assert tags_node.is_header is True
        assert tags_node.field_type == "list"
        assert len(tags_node.children) == 2

    def test_depth_tracking(self) -> None:
        values = {"api_url": "http://grafana:3000"}
        nodes = build_tree_from_schema(NestedConfig, values)
        labels_node = next(n for n in nodes if n.key == "labels")
        assert labels_node.depth == 0
        for child in labels_node.children:
            assert child.depth == 1

    def test_parent_references(self) -> None:
        nodes = build_tree_from_schema(NestedConfig, {"api_url": "http://grafana:3000"})
        labels_node = next(n for n in nodes if n.key == "labels")
        for child in labels_node.children:
            assert child.parent is labels_node

    def test_title_and_description(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        url_node = next(n for n in nodes if n.key == "api_url")
        assert url_node.title == "API URL"
        assert url_node.description == "The base URL for the API"


# ── _flatten_tree ─────────────────────────────────────────────────────


class TestFlattenTree:
    def test_flat_nodes(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        flat = _flatten_tree(nodes)
        assert len(flat) == len(nodes)

    def test_nested_nodes(self) -> None:
        nodes = build_tree_from_schema(NestedConfig, {"api_url": "http://x"})
        flat = _flatten_tree(nodes)
        # Should include top-level nodes + children of labels
        labels_node = next(n for n in nodes if n.key == "labels")
        expected_count = len(nodes) + len(labels_node.children)
        assert len(flat) == expected_count

    def test_order_preserved(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "additional_headers": {"X-Custom": "val"},
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        flat = _flatten_tree(nodes)
        # Headers should appear before their children in the flat list
        headers_idx = next(i for i, n in enumerate(flat) if n.key == "additional_headers")
        child_idx = next(i for i, n in enumerate(flat) if n.dict_key == "X-Custom")
        assert child_idx > headers_idx


# ── tree_to_dict ──────────────────────────────────────────────────────


class TestTreeToDict:
    def test_simple_roundtrip(self) -> None:
        values = {"api_url": "http://test:9090", "timeout": 60, "verify_ssl": False}
        nodes = build_tree_from_schema(SimpleConfig, values)
        result = tree_to_dict(nodes)
        assert result["api_url"] == "http://test:9090"
        assert result["timeout"] == 60
        assert result["verify_ssl"] is False

    def test_nested_roundtrip(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "labels": {"pod": "custom_pod", "namespace": "custom_ns"},
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        result = tree_to_dict(nodes)
        assert result["api_url"] == "http://grafana:3000"
        assert result["labels"]["pod"] == "custom_pod"
        assert result["labels"]["namespace"] == "custom_ns"

    def test_dict_field_roundtrip(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "additional_headers": {"Authorization": "Bearer token"},
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        result = tree_to_dict(nodes)
        assert result["additional_headers"]["Authorization"] == "Bearer token"

    def test_list_field_roundtrip(self) -> None:
        values = {
            "api_url": "http://grafana:3000",
            "tags": ["tag1", "tag2", "tag3"],
        }
        nodes = build_tree_from_schema(NestedConfig, values)
        result = tree_to_dict(nodes)
        assert result["tags"] == ["tag1", "tag2", "tag3"]

    def test_none_values_skipped(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        result = tree_to_dict(nodes)
        # api_key has default=None and no value set, should be skipped
        assert "api_key" not in result

    def test_defaults_preserved(self) -> None:
        nodes = build_tree_from_schema(SimpleConfig, {})
        result = tree_to_dict(nodes)
        assert result["verify_ssl"] is True
        assert result["timeout"] == 30

    def test_empty_dict_preserved(self) -> None:
        nodes = build_tree_from_schema(NestedConfig, {"api_url": "http://x"})
        headers_node = next(n for n in nodes if n.key == "additional_headers")
        # Headers should be empty (no children)
        assert headers_node.is_header is True
        result = tree_to_dict(nodes)
        # Empty headers should still produce an empty dict
        assert result.get("additional_headers") == {} or "additional_headers" not in result

    def test_empty_list_preserved(self) -> None:
        nodes = build_tree_from_schema(NestedConfig, {"api_url": "http://x"})
        tags_node = next(n for n in nodes if n.key == "tags")
        assert tags_node.is_header is True
        result = tree_to_dict(nodes)
        assert result.get("tags") == [] or "tags" not in result


# ── save_config_to_file ──────────────────────────────────────────────


class TestSaveConfigToFile:
    def test_save_to_new_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_dict = {"api_url": "http://test:9090", "timeout": 60}

        ok, msg = save_config_to_file(config_file, "test/toolset", config_dict)

        assert ok is True
        assert "saved" in msg.lower()
        assert config_file.exists()
        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert saved["toolsets"]["test/toolset"]["enabled"] is True
        assert saved["toolsets"]["test/toolset"]["config"]["api_url"] == "http://test:9090"

    def test_merge_into_existing(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        # Write existing config
        existing = {
            "model": "gpt-4",
            "toolsets": {
                "other/toolset": {"enabled": True, "config": {"url": "http://other"}},
            },
        }
        with open(config_file, "w") as f:
            yaml.dump(existing, f)

        ok, _msg = save_config_to_file(
            config_file, "new/toolset", {"api_url": "http://new:8080"}
        )

        assert ok is True
        with open(config_file) as f:
            saved = yaml.safe_load(f)

        # Existing settings preserved
        assert saved["model"] == "gpt-4"
        assert saved["toolsets"]["other/toolset"]["config"]["url"] == "http://other"
        # New toolset added
        assert saved["toolsets"]["new/toolset"]["enabled"] is True
        assert saved["toolsets"]["new/toolset"]["config"]["api_url"] == "http://new:8080"

    def test_replace_existing_toolset_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        existing = {
            "toolsets": {
                "test/toolset": {"enabled": True, "config": {"api_url": "old"}},
            },
        }
        with open(config_file, "w") as f:
            yaml.dump(existing, f)

        save_config_to_file(config_file, "test/toolset", {"api_url": "new"})

        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert saved["toolsets"]["test/toolset"]["config"]["api_url"] == "new"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        config_file = tmp_path / "subdir" / "deep" / "config.yaml"

        save_config_to_file(config_file, "test/toolset", {"key": "val"})

        assert config_file.exists()

    def test_handles_no_toolsets_section(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        existing = {"model": "gpt-4"}
        with open(config_file, "w") as f:
            yaml.dump(existing, f)

        save_config_to_file(config_file, "test/toolset", {"key": "val"})

        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert "toolsets" in saved
        assert saved["toolsets"]["test/toolset"]["config"]["key"] == "val"

    def test_handles_none_toolsets_section(self, tmp_path: Path) -> None:
        """YAML 'toolsets:' with no value parses as None."""
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump({"toolsets": None}, f)

        ok, _ = save_config_to_file(config_file, "test/toolset", {"key": "val"})
        assert ok is True
        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert saved["toolsets"]["test/toolset"]["config"]["key"] == "val"

    def test_handles_none_mcp_servers_section(self, tmp_path: Path) -> None:
        """YAML 'mcp_servers:' with no value parses as None."""
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump({"mcp_servers": None}, f)

        ok, _ = save_config_to_file(config_file, "jira_server", {"mode": "stdio"}, is_mcp=True)
        assert ok is True
        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert saved["mcp_servers"]["jira_server"]["config"]["mode"] == "stdio"

    def test_does_not_print_to_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config_file = tmp_path / "config.yaml"
        save_config_to_file(config_file, "test/toolset", {"key": "val"})

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


# ── run_config_test ───────────────────────────────────────────────────


class TestRunConfigTest:
    def test_passes_with_no_prerequisites(self) -> None:
        ts = ConfigurableToolset(prerequisites=[])
        ok, msg = run_config_test(ts, {"api_url": "http://test:9090"})
        assert ok is True
        assert "passed" in msg.lower()

    def test_fails_with_failing_callable(self) -> None:
        def failing_check(config: dict) -> tuple:
            return False, "Connection refused"

        ts = ConfigurableToolset(
            prerequisites=[CallablePrerequisite(callable=failing_check)]
        )
        ok, msg = run_config_test(ts, {"api_url": "http://bad:9090"})
        assert ok is False
        assert "Connection refused" in msg

    def test_does_not_mutate_original(self) -> None:
        ts = ConfigurableToolset(prerequisites=[])
        ts.status = ToolsetStatusEnum.DISABLED
        ts.error = None

        run_config_test(ts, {"api_url": "http://test"})

        # Original should be unchanged
        assert ts.status == ToolsetStatusEnum.DISABLED

    def test_allows_stdout_outside_tui(self, capsys: pytest.CaptureFixture[str]) -> None:
        """run_config_test is called outside the TUI so output flows freely."""

        def noisy_check(config: dict) -> tuple:
            print("NOISY STDOUT LINE")
            return True, ""

        ts = ConfigurableToolset(
            prerequisites=[CallablePrerequisite(callable=noisy_check)]
        )
        ok, msg = run_config_test(ts, {"api_url": "http://test"})

        assert ok is True

        # Output is no longer captured — it goes to the real terminal
        captured = capsys.readouterr()
        assert "NOISY" in captured.out


# ── select_toolset ────────────────────────────────────────────────────


class TestSelectToolset:
    def test_no_configurable_toolsets(self) -> None:
        console = Console(quiet=True)
        result = select_toolset([], console)
        assert result is None


# ── _get_existing_config ──────────────────────────────────────────────


class TestGetExistingConfig:
    def test_returns_empty_dict_when_no_existing_config(self) -> None:
        ts = ConfigurableToolset()
        config = MagicMock()
        config.toolsets = {}

        result = _get_existing_config(ts, config)
        assert result == {}

    def test_returns_empty_dict_when_toolsets_is_none(self) -> None:
        ts = ConfigurableToolset()
        config = MagicMock()
        config.toolsets = None

        result = _get_existing_config(ts, config)
        assert result == {}

    def test_returns_existing_config_when_present(self) -> None:
        ts = ConfigurableToolset()
        config = MagicMock()
        config.toolsets = {
            "test/configurable": {"config": {"api_url": "http://saved:9090", "timeout": 60}}
        }

        result = _get_existing_config(ts, config)
        assert result == {"api_url": "http://saved:9090", "timeout": 60}


# ── ConfigFieldNode manipulation ──────────────────────────────────────


class TestConfigFieldNodeManipulation:
    def test_add_dict_entry(self) -> None:
        node = ConfigFieldNode(
            key="headers", field_type="dict", is_header=True, depth=0
        )
        child = ConfigFieldNode(
            key="X-Custom", field_type="str", value="value", depth=1, parent=node
        )
        node.children.append(child)
        assert len(node.children) == 1
        assert node.children[0].key == "X-Custom"

    def test_remove_dict_entry(self) -> None:
        node = ConfigFieldNode(
            key="headers", field_type="dict", is_header=True, depth=0
        )
        child1 = ConfigFieldNode(
            key="A", field_type="str", value="1", depth=1, parent=node
        )
        child2 = ConfigFieldNode(
            key="B", field_type="str", value="2", depth=1, parent=node
        )
        node.children.extend([child1, child2])
        node.children.remove(child1)
        assert len(node.children) == 1
        assert node.children[0].key == "B"

    def test_add_list_entry(self) -> None:
        node = ConfigFieldNode(
            key="tags", field_type="list", is_header=True, depth=0
        )
        child = ConfigFieldNode(
            key="0", field_type="str", value="new_tag", depth=1, parent=node
        )
        node.children.append(child)
        result = tree_to_dict([node])
        assert result["tags"] == ["new_tag"]

    def test_remove_list_entry(self) -> None:
        node = ConfigFieldNode(
            key="tags", field_type="list", is_header=True, depth=0
        )
        c0 = ConfigFieldNode(key="0", field_type="str", value="a", depth=1, parent=node)
        c1 = ConfigFieldNode(key="1", field_type="str", value="b", depth=1, parent=node)
        node.children.extend([c0, c1])
        node.children.remove(c0)
        result = tree_to_dict([node])
        assert result["tags"] == ["b"]

    def test_bool_toggle(self) -> None:
        node = ConfigFieldNode(key="verify_ssl", field_type="bool", value=True, depth=0)
        node.value = not bool(node.value)
        assert node.value is False
        node.value = not bool(node.value)
        assert node.value is True

    def test_optional_field_set_to_null(self) -> None:
        """Optional fields can be set to None (displayed as <null>)."""
        node = ConfigFieldNode(
            key="api_key", field_type="str", value="secret", required=False, depth=0
        )
        node.value = None
        assert node.value is None

    def test_optional_field_null_not_explicitly_set_omitted(self) -> None:
        """When an optional field is None and not explicitly set, tree_to_dict omits it."""
        node = ConfigFieldNode(
            key="api_key", field_type="str", value=None, required=False, depth=0
        )
        result = tree_to_dict([node])
        assert "api_key" not in result

    def test_optional_field_null_explicitly_set_saved(self) -> None:
        """When an optional field is explicitly set to None, tree_to_dict includes it."""
        node = ConfigFieldNode(
            key="api_key", field_type="str", value=None, required=False, depth=0,
            explicitly_set=True,
        )
        result = tree_to_dict([node])
        assert "api_key" in result
        assert result["api_key"] is None

    def test_empty_string_preserved_in_tree_to_dict(self) -> None:
        """Empty string is saved as empty string, not as None."""
        node = ConfigFieldNode(
            key="api_key", field_type="str", value="", required=False, depth=0,
            explicitly_set=True,
        )
        result = tree_to_dict([node])
        assert result["api_key"] == ""

    def test_explicitly_set_from_existing_config(self) -> None:
        """Fields loaded from existing config are marked as explicitly_set."""
        values = {"api_url": "http://test:9090", "api_key": None}
        nodes = build_tree_from_schema(SimpleConfig, values)
        url_node = next(n for n in nodes if n.key == "api_url")
        key_node = next(n for n in nodes if n.key == "api_key")
        # Both are in current_values → explicitly_set
        assert url_node.explicitly_set is True
        assert key_node.explicitly_set is True
        # timeout is NOT in current_values → not explicitly_set
        timeout_node = next(n for n in nodes if n.key == "timeout")
        assert timeout_node.explicitly_set is False

    def test_required_field_cannot_be_null_display(self) -> None:
        """Required fields with None value display as empty, not <null>."""
        nodes = build_tree_from_schema(SimpleConfig, {})
        # api_url is required (no default)
        url_node = next(n for n in nodes if n.key == "api_url")
        assert url_node.required is True
        assert url_node.value is None

    def test_optional_field_starts_as_null(self) -> None:
        """Optional fields with no value should have None."""
        nodes = build_tree_from_schema(SimpleConfig, {})
        api_key_node = next(n for n in nodes if n.key == "api_key")
        assert api_key_node.required is False
        assert api_key_node.value is None


# ── Integration: real Pydantic configs ────────────────────────────────


class TestRealConfigSchemas:
    """Test with actual HolmesGPT config classes to verify compatibility."""

    def test_grafana_config_tree(self) -> None:
        from holmes.plugins.toolsets.grafana.common import GrafanaConfig

        values = {"api_url": "http://grafana:3000", "api_key": "secret123"}
        nodes = build_tree_from_schema(GrafanaConfig, values)
        names = {n.key for n in nodes}
        assert "api_url" in names
        assert "api_key" in names
        assert "verify_ssl" in names

        result = tree_to_dict(nodes)
        assert result["api_url"] == "http://grafana:3000"
        assert result["api_key"] == "secret123"

    def test_grafana_tempo_config_tree(self) -> None:
        from holmes.plugins.toolsets.grafana.common import GrafanaTempoConfig

        values = {
            "api_url": "http://tempo:3200",
            "labels": {"pod": "custom.pod"},
        }
        nodes = build_tree_from_schema(GrafanaTempoConfig, values)
        labels_node = next(n for n in nodes if n.key == "labels")
        assert labels_node.is_header is True
        assert labels_node.field_type == "model"

        pod_child = next(c for c in labels_node.children if c.key == "pod")
        assert pod_child.value == "custom.pod"

        result = tree_to_dict(nodes)
        assert result["labels"]["pod"] == "custom.pod"

    def test_full_roundtrip_with_grafana(self) -> None:
        """Build tree from GrafanaConfig, convert back to dict, verify equivalence."""
        from holmes.plugins.toolsets.grafana.common import GrafanaConfig

        original = {
            "api_url": "http://grafana:3000",
            "api_key": "my-key",
            "verify_ssl": False,
            "additional_headers": {"X-Custom": "value"},
        }
        nodes = build_tree_from_schema(GrafanaConfig, original)
        result = tree_to_dict(nodes)

        assert result["api_url"] == original["api_url"]
        assert result["api_key"] == original["api_key"]
        assert result["verify_ssl"] == original["verify_ssl"]
        assert result["additional_headers"] == original["additional_headers"]

    def test_mcp_config_tree(self) -> None:
        from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, MCPMode

        values = {"url": "http://example.com:8000/mcp", "mode": "sse"}
        nodes = build_tree_from_schema(MCPConfig, values)
        mode_node = next(n for n in nodes if n.key == "mode")
        assert mode_node.field_type == "enum"
        assert mode_node.enum_class is MCPMode
        assert mode_node.value == "sse"

    def test_mcp_config_mode_is_first_field(self) -> None:
        from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, StdioMCPConfig

        sse_nodes = build_tree_from_schema(MCPConfig, {})
        assert sse_nodes[0].key == "mode"

        stdio_nodes = build_tree_from_schema(StdioMCPConfig, {})
        assert stdio_nodes[0].key == "mode"

    def test_stdio_mcp_config_tree(self) -> None:
        from holmes.plugins.toolsets.mcp.toolset_mcp import StdioMCPConfig, MCPMode

        values = {"mode": "stdio", "command": "uvx", "args": ["mcp-atlassian"]}
        nodes = build_tree_from_schema(StdioMCPConfig, values)
        mode_node = next(n for n in nodes if n.key == "mode")
        assert mode_node.field_type == "enum"
        assert mode_node.value == "stdio"

        cmd_node = next(n for n in nodes if n.key == "command")
        assert cmd_node.value == "uvx"

        args_node = next(n for n in nodes if n.key == "args")
        assert args_node.is_header is True
        assert len(args_node.children) == 1
        assert args_node.children[0].value == "mcp-atlassian"

    def test_mcp_config_roundtrip(self) -> None:
        from holmes.plugins.toolsets.mcp.toolset_mcp import StdioMCPConfig

        original = {
            "mode": "stdio",
            "command": "uvx",
            "args": ["mcp-atlassian"],
            "env": {"JIRA_URL": "https://example.atlassian.net"},
        }
        nodes = build_tree_from_schema(StdioMCPConfig, original)
        result = tree_to_dict(nodes)
        assert result["mode"] == "stdio"
        assert result["command"] == "uvx"
        assert result["args"] == ["mcp-atlassian"]
        assert result["env"]["JIRA_URL"] == "https://example.atlassian.net"


# ── Enum support ─────────────────────────────────────────────────────


class SampleEnum(str, Enum):
    ALPHA = "alpha"
    BETA = "beta"
    GAMMA = "gamma"


class EnumConfig(ToolsetConfig):
    mode: SampleEnum = Field(default=SampleEnum.ALPHA, title="Mode")
    name: str = Field(default="", title="Name")


class AltEnumConfig(ToolsetConfig):
    mode: SampleEnum = Field(default=SampleEnum.BETA, title="Mode")
    extra_field: str = Field(default="", title="Extra")


class TestExtractEnumClass:
    def test_direct_enum(self) -> None:
        assert _extract_enum_class(SampleEnum) is SampleEnum

    def test_optional_enum(self) -> None:
        assert _extract_enum_class(Optional[SampleEnum]) is SampleEnum

    def test_non_enum(self) -> None:
        assert _extract_enum_class(str) is None
        assert _extract_enum_class(int) is None

    def test_none(self) -> None:
        assert _extract_enum_class(None) is None


class TestResolvePrimitiveTypeEnum:
    def test_enum(self) -> None:
        assert _resolve_primitive_type(SampleEnum) == "enum"

    def test_optional_enum(self) -> None:
        assert _resolve_primitive_type(Optional[SampleEnum]) == "enum"


class TestBuildTreeEnum:
    def test_enum_field_detected(self) -> None:
        nodes = build_tree_from_schema(EnumConfig, {})
        mode_node = next(n for n in nodes if n.key == "mode")
        assert mode_node.field_type == "enum"
        assert mode_node.enum_class is SampleEnum
        assert mode_node.value == "alpha"  # default

    def test_enum_with_current_value(self) -> None:
        nodes = build_tree_from_schema(EnumConfig, {"mode": "beta"})
        mode_node = next(n for n in nodes if n.key == "mode")
        assert mode_node.value == "beta"

    def test_enum_value_normalised_from_instance(self) -> None:
        nodes = build_tree_from_schema(EnumConfig, {"mode": SampleEnum.GAMMA})
        mode_node = next(n for n in nodes if n.key == "mode")
        assert mode_node.value == "gamma"
        assert isinstance(mode_node.value, str)

    def test_enum_roundtrip(self) -> None:
        nodes = build_tree_from_schema(EnumConfig, {"mode": "beta", "name": "test"})
        result = tree_to_dict(nodes)
        assert result["mode"] == "beta"
        assert result["name"] == "test"


class TestSelectConfigClass:
    def test_single_class(self) -> None:
        result = _select_config_class([EnumConfig], {})
        assert result is EnumConfig

    def test_picks_correct_class_by_discriminator(self) -> None:
        result = _select_config_class(
            [EnumConfig, AltEnumConfig], {"mode": "beta"}
        )
        assert result is AltEnumConfig

    def test_falls_back_to_first_class(self) -> None:
        result = _select_config_class(
            [EnumConfig, AltEnumConfig], {"mode": "gamma"}
        )
        assert result is EnumConfig

    def test_no_value_returns_first(self) -> None:
        result = _select_config_class([EnumConfig, AltEnumConfig], {})
        assert result is EnumConfig

    def test_field_matching_when_no_discriminator_value(self) -> None:
        """When discriminator is absent, pick class by matching non-discriminator fields."""
        # AltEnumConfig has 'extra_field', EnumConfig has 'name'
        result = _select_config_class(
            [EnumConfig, AltEnumConfig], {"extra_field": "hello"}
        )
        assert result is AltEnumConfig

    def test_field_matching_prefers_higher_overlap(self) -> None:
        result = _select_config_class(
            [EnumConfig, AltEnumConfig], {"name": "test"}
        )
        assert result is EnumConfig

    def test_mcp_config_classes(self) -> None:
        from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, MCPMode, StdioMCPConfig

        assert _select_config_class([MCPConfig, StdioMCPConfig], {"mode": "stdio"}) is StdioMCPConfig
        assert _select_config_class([MCPConfig, StdioMCPConfig], {"mode": "sse"}) is MCPConfig
        assert _select_config_class([MCPConfig, StdioMCPConfig], {"mode": "streamable-http"}) is MCPConfig


# ── MCP save/load ────────────────────────────────────────────────────


class TestSaveMCPConfig:
    def test_save_mcp_to_mcp_servers_section(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_dict = {"mode": "stdio", "command": "uvx", "args": ["mcp-atlassian"]}

        ok, msg = save_config_to_file(config_file, "jira_server", config_dict, is_mcp=True)

        assert ok is True
        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert "mcp_servers" in saved
        assert saved["mcp_servers"]["jira_server"]["config"]["mode"] == "stdio"
        assert "toolsets" not in saved

    def test_save_mcp_preserves_existing_fields(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        existing = {
            "mcp_servers": {
                "jira_server": {
                    "description": "Jira integration",
                    "llm_instructions": "Use this for Jira",
                    "config": {"mode": "sse", "url": "http://old"},
                }
            }
        }
        with open(config_file, "w") as f:
            yaml.dump(existing, f)

        save_config_to_file(
            config_file, "jira_server", {"mode": "stdio", "command": "uvx"}, is_mcp=True
        )

        with open(config_file) as f:
            saved = yaml.safe_load(f)
        entry = saved["mcp_servers"]["jira_server"]
        assert entry["description"] == "Jira integration"
        assert entry["llm_instructions"] == "Use this for Jira"
        assert entry["config"]["mode"] == "stdio"

    def test_regular_save_still_uses_toolsets(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        ok, _ = save_config_to_file(config_file, "grafana/dashboards", {"api_url": "http://x"})
        assert ok is True
        with open(config_file) as f:
            saved = yaml.safe_load(f)
        assert "toolsets" in saved
        assert saved["toolsets"]["grafana/dashboards"]["enabled"] is True


class TestGetExistingConfigMCP:
    def test_finds_mcp_config(self) -> None:
        ts = make_toolset("jira_server")
        ts.type = ToolsetType.MCP
        config = MagicMock()
        config.toolsets = {}
        config.mcp_servers = {
            "jira_server": {"config": {"mode": "stdio", "command": "uvx"}}
        }
        result = _get_existing_config(ts, config)
        assert result["mode"] == "stdio"
        assert result["command"] == "uvx"

    def test_prefers_toolsets_over_mcp_servers(self) -> None:
        ts = make_toolset("shared_name")
        ts.type = ToolsetType.MCP
        config = MagicMock()
        config.toolsets = {"shared_name": {"config": {"api_url": "http://toolset"}}}
        config.mcp_servers = {"shared_name": {"config": {"mode": "stdio"}}}
        result = _get_existing_config(ts, config)
        assert result["api_url"] == "http://toolset"

    def test_non_mcp_toolset_ignores_mcp_servers(self) -> None:
        ts = make_toolset("jira_server")  # type=None (not MCP)
        config = MagicMock()
        config.toolsets = {}
        config.mcp_servers = {
            "jira_server": {"config": {"mode": "stdio", "command": "uvx"}}
        }
        result = _get_existing_config(ts, config)
        assert result == {}


# ── Per-class config cache (mode cycling) ────────────────────────────


class TestConfigClassCaching:
    """Simulate the per-class caching that run_tree_editor uses when cycling modes."""

    def test_roundtrip_preserves_values_across_class_switch(self) -> None:
        """Cycling stdio → sse → stdio must preserve the original stdio fields."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig, StdioMCPConfig

        config_classes = [MCPConfig, StdioMCPConfig]
        cache: Dict[Type[BaseModel], Dict[str, Any]] = {}

        # Start with stdio config
        stdio_values = {
            "mode": "stdio",
            "command": "uvx",
            "args": ["mcp-atlassian"],
            "env": {"JIRA_URL": "https://example.com"},
        }
        current_class = _select_config_class(config_classes, stdio_values)
        assert current_class is StdioMCPConfig
        cache[current_class] = dict(stdio_values)

        nodes = build_tree_from_schema(current_class, stdio_values)

        # Simulate switching to SSE (cache current, load new)
        cache[current_class] = tree_to_dict(nodes)
        new_class = MCPConfig
        restored = dict(cache.get(new_class, {}))
        restored["mode"] = "sse"
        nodes = build_tree_from_schema(new_class, restored)
        current_class = new_class

        # Verify SSE fields are present
        field_keys = {n.key for n in nodes}
        assert "url" in field_keys
        assert "command" not in field_keys

        # Simulate switching back to STDIO (cache current, load from cache)
        cache[current_class] = tree_to_dict(nodes)
        new_class = StdioMCPConfig
        restored = dict(cache.get(new_class, {}))
        restored["mode"] = "stdio"
        nodes = build_tree_from_schema(new_class, restored)

        # The original stdio values should be fully restored
        result = tree_to_dict(nodes)
        assert result["command"] == "uvx"
        assert result["args"] == ["mcp-atlassian"]
        assert result["env"] == {"JIRA_URL": "https://example.com"}
