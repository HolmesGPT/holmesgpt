"""Tests for MA_HOLMES_CHAT RBAC cluster filtering in the robusta toolset.

PR #1478 added cross-cluster access to the robusta tools (all_clusters / clusters
params, plus fetch_finding_by_id which looks up a finding regardless of cluster).
These tests verify that when a request carries a user id, the tools only ever
touch clusters the user has MA_HOLMES_CHAT permission on.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.robusta.robusta import (
    FetchConfigurationChangesMetadata,
    FetchResourceIssuesMetadata,
    FetchResourceRecommendation,
    FetchRobustaFinding,
    _check_finding_cluster_access,
    _resolve_authorized_clusters,
)


def _dal(allowed, cluster="current"):
    """A mock DAL whose get_holmes_chat_allowed_clusters returns `allowed`."""
    dal = Mock()
    dal.enabled = True
    dal.cluster = cluster
    dal.get_holmes_chat_allowed_clusters.return_value = allowed
    return dal


def _ctx(user_id):
    return SimpleNamespace(request_context={"user_id": user_id} if user_id else {})


class TestResolveAuthorizedClusters:
    def test_no_user_id_skips_filtering(self):
        dal = _dal(allowed=[])  # would deny everything if consulted
        clusters, err = _resolve_authorized_clusters(
            {"all_clusters": True}, dal, user_id=None
        )
        assert err is None
        assert clusters == ["*"]
        dal.get_holmes_chat_allowed_clusters.assert_not_called()

    def test_regular_user_full_access(self):
        dal = _dal(allowed=None)  # None => regular account user
        clusters, err = _resolve_authorized_clusters(
            {"all_clusters": True}, dal, user_id="u1"
        )
        assert err is None
        assert clusters == ["*"]

    def test_no_allowed_clusters_denied(self):
        dal = _dal(allowed=[])
        clusters, err = _resolve_authorized_clusters(
            {"all_clusters": True}, dal, user_id="u1"
        )
        assert clusters is None
        assert err is not None

    def test_all_clusters_narrowed_to_allowed(self):
        dal = _dal(allowed=["a", "b"])
        clusters, err = _resolve_authorized_clusters(
            {"all_clusters": True}, dal, user_id="u1"
        )
        assert err is None
        assert clusters == ["a", "b"]

    def test_explicit_clusters_intersected(self):
        dal = _dal(allowed=["a", "b"])
        clusters, err = _resolve_authorized_clusters(
            {"clusters": ["a", "c"]}, dal, user_id="u1"
        )
        assert err is None
        assert clusters == ["a"]

    def test_explicit_clusters_all_unauthorized_denied(self):
        dal = _dal(allowed=["a", "b"])
        clusters, err = _resolve_authorized_clusters(
            {"clusters": ["c", "d"]}, dal, user_id="u1"
        )
        assert clusters is None
        assert err is not None

    def test_current_cluster_allowed(self):
        dal = _dal(allowed=["current", "a"], cluster="current")
        clusters, err = _resolve_authorized_clusters({}, dal, user_id="u1")
        assert err is None
        assert clusters == ["current"]

    def test_current_cluster_not_allowed_denied(self):
        dal = _dal(allowed=["a"], cluster="current")
        clusters, err = _resolve_authorized_clusters({}, dal, user_id="u1")
        assert clusters is None
        assert err is not None


class TestCheckFindingClusterAccess:
    def test_no_user_id_allows(self):
        dal = _dal(allowed=[])
        assert _check_finding_cluster_access({"cluster": "a"}, dal, None) is None

    def test_regular_user_allows(self):
        dal = _dal(allowed=None)
        assert _check_finding_cluster_access({"cluster": "a"}, dal, "u1") is None

    def test_authorized_cluster_allows(self):
        dal = _dal(allowed=["a", "b"])
        assert _check_finding_cluster_access({"cluster": "a"}, dal, "u1") is None

    def test_unauthorized_cluster_denied(self):
        dal = _dal(allowed=["a"])
        assert _check_finding_cluster_access({"cluster": "b"}, dal, "u1") is not None

    def test_external_finding_denied_for_restricted_user(self):
        dal = _dal(allowed=["a"])
        assert _check_finding_cluster_access({"cluster": None}, dal, "u1") is not None


class TestToolInvokeEnforcement:
    """End-to-end style checks that the tool _invoke methods deny unauthorized
    cross-cluster requests before hitting the DAL data fetchers."""

    def test_issues_tool_denies_unauthorized_cluster(self):
        dal = _dal(allowed=["a"])
        tool = FetchResourceIssuesMetadata(dal)
        result = tool._invoke(
            {
                "start_datetime": "2024-01-01T00:00:00Z",
                "end_datetime": "2024-01-02T00:00:00Z",
                "clusters": ["b"],
            },
            context=_ctx("u1"),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        dal.get_issues_metadata.assert_not_called()

    def test_issues_tool_filters_all_clusters(self):
        dal = _dal(allowed=["a", "b"])
        dal.get_issues_metadata.return_value = [{"id": "1"}]
        tool = FetchResourceIssuesMetadata(dal)
        result = tool._invoke(
            {
                "start_datetime": "2024-01-01T00:00:00Z",
                "end_datetime": "2024-01-02T00:00:00Z",
                "all_clusters": True,
            },
            context=_ctx("u1"),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = dal.get_issues_metadata.call_args
        assert kwargs["clusters"] == ["a", "b"]

    def test_config_changes_tool_denies_unauthorized_cluster(self):
        dal = _dal(allowed=["a"])
        tool = FetchConfigurationChangesMetadata(dal)
        result = tool._invoke(
            {
                "start_datetime": "2024-01-01T00:00:00Z",
                "end_datetime": "2024-01-02T00:00:00Z",
                "clusters": ["b"],
            },
            context=_ctx("u1"),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        dal.get_issues_metadata.assert_not_called()

    def test_recommendation_tool_denies_unauthorized_cluster(self):
        dal = _dal(allowed=["a"])
        tool = FetchResourceRecommendation(dal)
        result = tool._invoke({"clusters": ["b"]}, context=_ctx("u1"))
        assert result.status == StructuredToolResultStatus.ERROR
        dal.get_resource_recommendation.assert_not_called()

    def test_finding_tool_denies_unauthorized_finding(self):
        dal = _dal(allowed=["a"])
        dal.enabled = True
        dal.get_issue_data.return_value = {"id": "f1", "cluster": "b"}
        tool = FetchRobustaFinding(dal)
        result = tool._invoke({"id": "f1"}, context=_ctx("u1"))
        assert result.status == StructuredToolResultStatus.ERROR

    def test_finding_tool_allows_authorized_finding(self):
        dal = _dal(allowed=["a"])
        dal.enabled = True
        dal.get_issue_data.return_value = {"id": "f1", "cluster": "a"}
        tool = FetchRobustaFinding(dal)
        result = tool._invoke({"id": "f1"}, context=_ctx("u1"))
        assert result.status == StructuredToolResultStatus.SUCCESS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
