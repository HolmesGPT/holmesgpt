"""Tests for the smart auto-enable logic for toolsets."""

from typing import ClassVar, Dict, List, Optional, Type

import pytest
from pydantic import BaseModel, Field

from holmes.core.tools import Toolset, ToolsetTag
from holmes.utils.pydantic_utils import ToolsetConfig


# --- Test config classes ---


class AllOptionalConfig(ToolsetConfig):
    """Config where every field has a default."""
    url: Optional[str] = Field(default=None)
    timeout: int = Field(default=30)


class RequiredFieldConfig(ToolsetConfig):
    """Config with a required field (no default)."""
    api_url: str = Field(title="API URL")
    api_key: Optional[str] = Field(default=None)


# --- Helpers ---


def _make_toolset(
    name: str = "test",
    enabled: bool = False,
    is_default: bool = False,
    config_classes: Optional[List[Type[BaseModel]]] = None,
    config: Optional[dict] = None,
) -> Toolset:
    kwargs = dict(
        name=name,
        enabled=enabled,
        is_default=is_default,
        description="test toolset",
        tools=[],
        tags=[ToolsetTag.CORE],
    )
    if config is not None:
        kwargs["config"] = config

    # Create a per-test subclass so config_classes doesn't leak between tests
    cls_attrs: dict = {}
    if config_classes is not None:
        cls_attrs["config_classes"] = config_classes
    subclass = type("TestToolset", (Toolset,), cls_attrs)

    return subclass(**kwargs)


# --- ToolsetConfig.has_required_fields tests ---


class TestHasRequiredFields:
    def test_all_optional_config(self):
        assert AllOptionalConfig.has_required_fields() is False

    def test_required_field_config(self):
        assert RequiredFieldConfig.has_required_fields() is True

    def test_base_toolset_config(self):
        """Base ToolsetConfig has no fields, so no required fields."""
        assert ToolsetConfig.has_required_fields() is False


# --- Toolset.should_auto_enable tests ---


class TestShouldAutoEnable:
    def test_already_enabled(self):
        """Toolset that is already enabled stays enabled."""
        toolset = _make_toolset(enabled=True, config_classes=[RequiredFieldConfig])
        assert toolset.should_auto_enable() is True

    def test_is_default(self):
        """Toolset marked as is_default is always auto-enabled."""
        toolset = _make_toolset(is_default=True, config_classes=[RequiredFieldConfig])
        assert toolset.should_auto_enable() is True

    def test_no_config_classes(self):
        """YAML-style toolset with no config_classes is auto-enabled."""
        toolset = _make_toolset(config_classes=[])
        assert toolset.should_auto_enable() is True

    def test_all_optional_config_no_config_provided(self):
        """Toolset where all config fields have defaults is auto-enabled even without config."""
        toolset = _make_toolset(config_classes=[AllOptionalConfig])
        assert toolset.should_auto_enable() is True

    def test_required_config_with_config_provided(self):
        """Toolset with required config AND config provided is auto-enabled."""
        toolset = _make_toolset(
            config_classes=[RequiredFieldConfig],
            config={"api_url": "http://example.com"},
        )
        assert toolset.should_auto_enable() is True

    def test_required_config_without_config(self):
        """Toolset with required config AND no config is NOT auto-enabled."""
        toolset = _make_toolset(config_classes=[RequiredFieldConfig])
        assert toolset.should_auto_enable() is False

    def test_required_config_with_empty_config(self):
        """Toolset with required config AND explicitly empty config ({}) is auto-enabled."""
        toolset = _make_toolset(
            config_classes=[RequiredFieldConfig],
            config={},
        )
        assert toolset.should_auto_enable() is True

    def test_disabled_by_default_no_config_classes(self):
        """Even a disabled toolset with no config classes should auto-enable."""
        toolset = _make_toolset(enabled=False, config_classes=[])
        assert toolset.should_auto_enable() is True
