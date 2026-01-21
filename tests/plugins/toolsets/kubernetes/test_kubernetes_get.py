import types
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from holmes.plugins.toolsets.kubernetes_get import (
    KubernetesGetResources,
    KubernetesGetToolset,
)
from holmes.core.tools import StructuredToolResultStatus


class _FakeResponse:
    def __init__(self, body: Dict[str, Any]):
        self._body = body

    def to_dict(self):
        return self._body


class _FakeResource:
    def __init__(self, namespaced: bool = True, kind: str = "Pod"):
        self.namespaced = namespaced
        self.kind = kind
        self.calls: List[Dict[str, Any]] = []
        self._responses: List[_FakeResponse] = []

    def queue_response(self, body: Dict[str, Any]):
        self._responses.append(_FakeResponse(body))

    def get(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("No queued responses")
        return self._responses.pop(0)


def _create_tool():
    toolset = KubernetesGetToolset()
    # direct access to tool instance
    return toolset.tools[0], toolset


def test_kubernetes_get_streams_pages_and_filters():
    tool, _ = _create_tool()
    resource = _FakeResource(namespaced=True, kind="Pod")
    resource.queue_response(
        {
            "items": [
                {"metadata": {"name": "keep-me", "namespace": "ns"}, "kind": "Pod"},
                {"metadata": {"name": "skip-me", "namespace": "ns"}, "kind": "Pod"},
            ],
            "metadata": {"continue": "token-1"},
        }
    )
    resource.queue_response(
        {
            "items": [
                {"metadata": {"name": "keep-too", "namespace": "ns"}, "kind": "Pod"}
            ],
            "metadata": {},
        }
    )

    params = {
        "api_version": "v1",
        "kind": "pods",
        "namespace": "ns",
        "client_filter": "keep",
        "filter_paths": ["metadata.name"],
        "chunk_size": 1,
    }

    with patch(
        "holmes.plugins.toolsets.kubernetes_get._get_dynamic_client"
    ) as mock_client, patch(
        "holmes.plugins.toolsets.kubernetes_get._resolve_resource", return_value=resource
    ):
        mock_client.return_value = types.SimpleNamespace()
        result = tool._invoke(params, context=None)  # type: ignore[arg-type]

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data is not None
    summary = result.data["summary"]
    assert summary["pages_fetched"] == 2
    assert summary["items_processed"] == 3
    assert summary["items_matched"] == 2
    assert summary["last_continue_token"] is None
    assert result.data["count"] == 2
    assert [item["name"] for item in result.data["items"]] == ["keep-me", "keep-too"]
    assert resource.calls[0]["limit"] == 1


def test_kubernetes_get_stops_when_max_results_reached():
    tool, _ = _create_tool()
    resource = _FakeResource(namespaced=False, kind="Node")
    resource.queue_response(
        {
            "items": [
                {"metadata": {"name": "node-a"}, "kind": "Node"},
                {"metadata": {"name": "node-b"}, "kind": "Node"},
            ],
            "metadata": {"_continue": "next"},
        }
    )

    params = {
        "api_version": "v1",
        "kind": "nodes",
        "max_results": 1,
        "client_filter": "node",
    }

    with patch(
        "holmes.plugins.toolsets.kubernetes_get._get_dynamic_client"
    ) as mock_client, patch(
        "holmes.plugins.toolsets.kubernetes_get._resolve_resource", return_value=resource
    ):
        mock_client.return_value = types.SimpleNamespace()
        result = tool._invoke(params, context=None)  # type: ignore[arg-type]

    assert result.status == StructuredToolResultStatus.SUCCESS
    summary = result.data["summary"]
    assert summary["items_matched"] == 1
    assert summary["truncated"] is True
    assert summary["last_continue_token"] == "next"
    assert len(result.data["items"]) == 1


def test_kubernetes_get_returns_error_on_resolution_failure():
    tool, _ = _create_tool()
    params = {"api_version": "v1", "kind": "pods"}

    with patch(
        "holmes.plugins.toolsets.kubernetes_get._get_dynamic_client"
    ) as mock_client, patch(
        "holmes.plugins.toolsets.kubernetes_get._resolve_resource",
        side_effect=Exception("boom"),
    ):
        mock_client.return_value = types.SimpleNamespace()
        result = tool._invoke(params, context=None)  # type: ignore[arg-type]

    assert result.status == StructuredToolResultStatus.ERROR
    assert "boom" in result.error


def test_kubernetes_get_count_only_mode():
    tool, _ = _create_tool()
    resource = _FakeResource(namespaced=False, kind="Node")
    resource.queue_response(
        {
            "items": [
                {"metadata": {"name": "node-a"}, "kind": "Node"},
                {"metadata": {"name": "node-b"}, "kind": "Node"},
            ],
            "metadata": {},
        }
    )

    params = {
        "api_version": "v1",
        "kind": "nodes",
        "output_format": "count",
    }

    with patch(
        "holmes.plugins.toolsets.kubernetes_get._get_dynamic_client"
    ) as mock_client, patch(
        "holmes.plugins.toolsets.kubernetes_get._resolve_resource", return_value=resource
    ):
        mock_client.return_value = types.SimpleNamespace()
        result = tool._invoke(params, context=None)  # type: ignore[arg-type]

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data["count"] == 2
    assert "items" not in result.data
