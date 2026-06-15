"""Unit tests for SupabaseDal.get_resource_recommendation method."""

import logging
from unittest.mock import Mock, patch

import pytest
from postgrest.exceptions import APIError as PGAPIError

from holmes.core.supabase_dal import (
    FIREWALL_TROUBLESHOOTING_URL,
    GROUPED_ISSUES_TABLE,
    ISSUES_TABLE,
    SupabaseConnectionException,
    SupabaseDal,
    SupabaseDnsException,
)


class TestSignIn:
    """Tests for SupabaseDal.sign_in() error classification.

    A firewall / egress policy that blocks the cluster from reaching the Robusta
    platform surfaces as a connection reset/refused during sign-in. Holmes should
    convert that into a SupabaseConnectionException whose message points the user
    at their firewall, instead of leaking a raw httpx traceback. Genuine auth
    errors must still propagate unchanged.
    """

    @pytest.fixture
    def mock_dal(self):
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.client = Mock()
            dal.url = "https://sp.eu.robusta.dev"
            dal.email = "user@example.com"
            dal.password = "secret"
            return dal

    def test_connection_reset_raises_firewall_exception(self, mock_dal, caplog):
        # The exact error Aviva hit at startup (ROB-273): httpx surfaces the
        # firewall block as "[Errno 104] Connection reset by peer".
        mock_dal.client.auth.sign_in_with_password.side_effect = Exception(
            "[Errno 104] Connection reset by peer"
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(SupabaseConnectionException) as exc_info:
                mock_dal.sign_in()

        # The exception stays a thin technical wrapper - it names the platform and
        # the underlying error but carries none of the actionable guidance.
        message = str(exc_info.value)
        assert "Robusta platform" in message
        assert "curl" not in message
        assert "*.robusta.dev" not in message
        assert FIREWALL_TROUBLESHOOTING_URL not in message

        # All the firewall guidance - cause, the allowlist fix, and the docs link -
        # is logged at WARNING (not ERROR, so it doesn't raise a Sentry alert)
        # before the exception is raised.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("firewall" in r.getMessage().lower() for r in warnings)
        assert any("*.robusta.dev" in r.getMessage() for r in warnings)
        assert any(FIREWALL_TROUBLESHOOTING_URL in r.getMessage() for r in warnings)

    def test_connection_refused_raises_firewall_exception(self, mock_dal):
        mock_dal.client.auth.sign_in_with_password.side_effect = (
            ConnectionRefusedError("[Errno 111] Connection refused")
        )
        with pytest.raises(SupabaseConnectionException):
            mock_dal.sign_in()

    def test_timeout_raises_firewall_exception(self, mock_dal):
        mock_dal.client.auth.sign_in_with_password.side_effect = TimeoutError(
            "connection timed out"
        )
        with pytest.raises(SupabaseConnectionException):
            mock_dal.sign_in()

    def test_dns_error_still_raises_dns_exception(self, mock_dal):
        mock_dal.client.auth.sign_in_with_password.side_effect = Exception(
            "Temporary failure in name resolution"
        )
        with pytest.raises(SupabaseDnsException):
            mock_dal.sign_in()

    def test_auth_error_is_not_wrapped(self, mock_dal):
        # A genuine credential error is not a connectivity/firewall problem;
        # wrapping it would mislead the user, so it must propagate unchanged.
        original = ValueError("Invalid login credentials")
        mock_dal.client.auth.sign_in_with_password.side_effect = original
        with pytest.raises(ValueError) as exc_info:
            mock_dal.sign_in()
        assert exc_info.value is original

    def test_successful_sign_in_returns_user_id(self, mock_dal):
        session = Mock(access_token="access-token", refresh_token="refresh-token")
        res = Mock(session=session, user=Mock(id="user-123"))
        mock_dal.client.auth.sign_in_with_password.return_value = res

        assert mock_dal.sign_in() == "user-123"
        mock_dal.client.auth.set_session.assert_called_once_with(
            "access-token", "refresh-token"
        )
        mock_dal.client.postgrest.auth.assert_called_once_with("access-token")


class TestIsRealtimeEnabled:
    """Tests for SupabaseDal.is_realtime_enabled()."""

    @pytest.fixture
    def mock_dal(self):
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"
            dal.client = Mock()
            return dal

    def _set_rpc_result(self, mock_dal, *, data=None, raise_exc=None):
        rpc_chain = Mock()
        if raise_exc is not None:
            rpc_chain.execute.side_effect = raise_exc
        else:
            res = Mock()
            res.data = data
            rpc_chain.execute.return_value = res
        mock_dal.client.rpc.return_value = rpc_chain
        return rpc_chain

    def test_returns_true_when_rpc_returns_true(self, mock_dal):
        self._set_rpc_result(mock_dal, data=True)
        assert mock_dal.is_realtime_enabled() is True
        mock_dal.client.rpc.assert_called_once_with("is_realtime_enabled", {})

    def test_returns_false_when_rpc_returns_false(self, mock_dal):
        self._set_rpc_result(mock_dal, data=False)
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_false_when_rpc_returns_list_of_false(self, mock_dal):
        # Some PostgREST responses wrap scalar return values in a single-row list.
        self._set_rpc_result(mock_dal, data=[False])
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_true_when_rpc_returns_list_of_true(self, mock_dal):
        self._set_rpc_result(mock_dal, data=[True])
        assert mock_dal.is_realtime_enabled() is True

    def test_returns_false_when_rpc_does_not_exist_pgrst202(self, mock_dal):
        exc = PGAPIError(
            {"code": "PGRST202", "message": "Could not find the function"}
        )
        self._set_rpc_result(mock_dal, raise_exc=exc)
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_false_when_rpc_does_not_exist_message_match(self, mock_dal):
        exc = PGAPIError(
            {
                "code": "OTHER",
                "message": "Could not find the function public.is_realtime_enabled",
            }
        )
        self._set_rpc_result(mock_dal, raise_exc=exc)
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_none_on_other_api_error(self, mock_dal):
        exc = PGAPIError({"code": "PGRST301", "message": "JWT expired"})
        self._set_rpc_result(mock_dal, raise_exc=exc)
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_on_connectivity_error(self, mock_dal):
        self._set_rpc_result(mock_dal, raise_exc=ConnectionError("network down"))
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_when_dal_disabled(self, mock_dal):
        mock_dal.enabled = False
        assert mock_dal.is_realtime_enabled() is None
        mock_dal.client.rpc.assert_not_called()

    def test_returns_none_on_empty_list_response(self, mock_dal):
        # An empty list from PostgREST means no rows — there's no value to
        # coerce, so we should treat it as inconclusive rather than
        # collapsing to False.
        self._set_rpc_result(mock_dal, data=[])
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_on_null_data(self, mock_dal):
        # Likewise, an explicit None payload is inconclusive — not a
        # definitive False.
        self._set_rpc_result(mock_dal, data=None)
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_true_for_dict_with_enabled_true(self, mock_dal):
        # A SQL function variant could return a row instead of a scalar.
        self._set_rpc_result(mock_dal, data={"enabled": True})
        assert mock_dal.is_realtime_enabled() is True

    def test_returns_false_for_dict_with_enabled_false(self, mock_dal):
        # And the same row shape with the field set to false. Naive
        # bool(data) would have wrongly returned True here.
        self._set_rpc_result(mock_dal, data={"enabled": False})
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_true_for_dict_with_enabled_truthy_in_list(self, mock_dal):
        self._set_rpc_result(mock_dal, data=[{"enabled": True}])
        assert mock_dal.is_realtime_enabled() is True

    def test_returns_none_for_dict_without_enabled_key(self, mock_dal):
        # Unknown dict shape — refuse to guess.
        self._set_rpc_result(mock_dal, data={"other": True})
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_for_unexpected_payload_type(self, mock_dal):
        # A string (or any other unexpected type) is inconclusive — we
        # won't fall back to truthy/falsy coercion.
        self._set_rpc_result(mock_dal, data="true")
        assert mock_dal.is_realtime_enabled() is None


class TestGetIssueDataFiring:
    """Tests that get_issue_data exposes a uniform `firing` boolean.

    The firing state is what tells Holmes whether an alert/issue is currently
    active or already resolved. For prometheus alerts it comes from the explicit
    `firing` column on GroupedIssues; for every other source it is derived from
    `ends_at` (null => still firing).
    """

    @pytest.fixture
    def mock_dal(self):
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"
            dal.client = Mock()
            return dal

    def _setup_tables(self, mock_dal, issue_row, grouped_row=None):
        """Wire client.table() so the Issues/GroupedIssues/Evidence lookups in
        get_issue_data resolve to the supplied rows (Evidence is left empty)."""

        def make_single_row_chain(row):
            chain = Mock()
            chain.select.return_value = chain
            chain.filter.return_value = chain
            res = Mock()
            res.data = [row] if row is not None else []
            chain.execute.return_value = res
            return chain

        # Evidence query: select().eq().not_.in_().execute() -> empty data
        evidence_chain = Mock()
        evidence_chain.select.return_value = evidence_chain
        evidence_chain.eq.return_value = evidence_chain
        evidence_chain.in_.return_value = evidence_chain
        evidence_chain.not_ = evidence_chain
        evidence_res = Mock()
        evidence_res.data = []
        evidence_chain.execute.return_value = evidence_res

        issue_chain = make_single_row_chain(issue_row)
        grouped_chain = make_single_row_chain(grouped_row)

        def table_side_effect(table_name):
            if table_name == ISSUES_TABLE:
                return issue_chain
            if table_name == GROUPED_ISSUES_TABLE:
                return grouped_chain
            return evidence_chain

        mock_dal.client.table.side_effect = table_side_effect

    def test_non_prometheus_firing_when_ends_at_is_none(self, mock_dal):
        self._setup_tables(
            mock_dal,
            issue_row={"id": "abc", "source": "kubernetes", "ends_at": None},
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        assert data["firing"] is True

    def test_non_prometheus_resolved_when_ends_at_is_set(self, mock_dal):
        self._setup_tables(
            mock_dal,
            issue_row={
                "id": "abc",
                "source": "kubernetes",
                "ends_at": "2026-06-07T10:00:00Z",
            },
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        assert data["firing"] is False

    def test_prometheus_uses_explicit_grouped_issues_firing_flag(self, mock_dal):
        # The Issues row points at prometheus, so get_issue_data re-fetches the
        # GroupedIssues row, which carries the explicit firing flag. A resolved
        # alert keeps firing=False even though we don't recompute it.
        self._setup_tables(
            mock_dal,
            issue_row={"id": "abc", "source": "prometheus", "ends_at": None},
            grouped_row={
                "id": "abc",
                "source": "prometheus",
                "firing": False,
                "ends_at": "2026-06-07T10:00:00Z",
            },
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        # Explicit flag from GroupedIssues is preserved, not overwritten.
        assert data["firing"] is False

    def test_prometheus_firing_flag_true_is_preserved(self, mock_dal):
        self._setup_tables(
            mock_dal,
            issue_row={"id": "abc", "source": "prometheus", "ends_at": None},
            grouped_row={
                "id": "abc",
                "source": "prometheus",
                "firing": True,
                "ends_at": None,
            },
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        assert data["firing"] is True


class TestGetResourceRecommendation:
    """Test cases for SupabaseDal.get_resource_recommendation method."""

    @pytest.fixture
    def mock_dal(self):
        """Create a SupabaseDal instance with mocked Supabase client."""
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"
            dal.client = Mock()
            return dal

    def _create_mock_scan_result(
        self,
        name: str,
        namespace: str,
        kind: str,
        container: str,
        cpu_req_allocated: str,
        cpu_req_recommended: str,
        cpu_lim_allocated: str,
        cpu_lim_recommended: str,
        mem_req_allocated: str,
        mem_req_recommended: str,
        mem_lim_allocated: str,
        mem_lim_recommended: str,
        priority: int = 5,
    ):
        """Helper to create a mock scan result with realistic KRR structure."""
        return {
            "name": name,
            "namespace": namespace,
            "kind": kind,
            "container": container,
            "priority": priority,
            "content": [
                {
                    "resource": "cpu",
                    "allocated": {
                        "request": cpu_req_allocated,
                        "limit": cpu_lim_allocated,
                    },
                    "recommended": {
                        "request": cpu_req_recommended,
                        "limit": cpu_lim_recommended,
                    },
                },
                {
                    "resource": "memory",
                    "allocated": {
                        "request": mem_req_allocated,
                        "limit": mem_lim_allocated,
                    },
                    "recommended": {
                        "request": mem_req_recommended,
                        "limit": mem_lim_recommended,
                    },
                },
            ],
        }

    def _setup_mock_query_chain(
        self, mock_dal, scan_meta_data, scan_results_data, sort_by=None
    ):
        """Set up the mock query chain for table().select().eq()...execute().

        The new implementation uses:
        - Meta query: select().eq(account_id).eq(latest).in_(cluster_id) or without in_
        - Results query: select().eq(account_id).or_(...).eq/like/order/limit
        """
        # Mock the scan metadata query
        meta_execute_result = Mock()
        meta_execute_result.data = scan_meta_data

        # Build the chain for metadata table with flexible chaining
        meta_chain = Mock()
        meta_chain.eq.return_value = meta_chain
        meta_chain.in_.return_value = meta_chain
        meta_chain.execute.return_value = meta_execute_result

        meta_table = Mock()
        meta_table.select.return_value = meta_chain

        # Mock the scan results query
        results_execute_result = Mock()
        results_execute_result.data = scan_results_data

        # Build the chain for results table with flexible chaining
        results_chain = Mock()
        results_chain.eq.return_value = results_chain
        results_chain.or_.return_value = results_chain
        results_chain.like.return_value = results_chain
        results_chain.order.return_value = results_chain
        results_chain.limit.return_value = results_chain
        results_chain.execute.return_value = results_execute_result

        results_table = Mock()
        results_table.select.return_value = results_chain

        # Mock table() to return appropriate mock based on table name
        def table_side_effect(table_name):
            if table_name == "ScansMeta":
                return meta_table
            elif table_name == "ScansResults":
                return results_table
            return Mock()

        mock_dal.client.table.side_effect = table_side_effect

        return meta_chain, results_chain

    def test_basic_functionality_default_params(self, mock_dal):
        """Test basic functionality with default parameters."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="app-1",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="2000m",
                cpu_lim_recommended="1000m",
                mem_req_allocated="1Gi",
                mem_req_recommended="512Mi",
                mem_lim_allocated="2Gi",
                mem_lim_recommended="1Gi",
            ),
            self._create_mock_scan_result(
                name="app-2",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="2000m",
                cpu_req_recommended="1000m",
                cpu_lim_allocated="4000m",
                cpu_lim_recommended="2000m",
                mem_req_allocated="2Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="4Gi",
                mem_lim_recommended="2Gi",
            ),
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call the method with default parameters
        results = mock_dal.get_resource_recommendation()

        # Verify results
        assert results is not None
        assert len(results) == 2
        # app-2 should be first (higher CPU savings: 3.0 cores vs 1.5 cores)
        assert results[0]["name"] == "app-2"
        assert results[1]["name"] == "app-1"

    def test_limit_parameter(self, mock_dal):
        """Test that limit parameter correctly limits results."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name=f"app-{i}",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
            )
            for i in range(20)
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Test with limit=5
        results = mock_dal.get_resource_recommendation(limit=5)

        assert results is not None
        assert len(results) == 5

    def test_sort_by_memory_total(self, mock_dal):
        """Test sorting by memory_total."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="low-memory",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="100m",
                cpu_req_recommended="100m",
                cpu_lim_allocated="100m",
                cpu_lim_recommended="100m",
                mem_req_allocated="1Gi",
                mem_req_recommended="512Mi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="512Mi",
            ),
            self._create_mock_scan_result(
                name="high-memory",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="100m",
                cpu_req_recommended="100m",
                cpu_lim_allocated="100m",
                cpu_lim_recommended="100m",
                mem_req_allocated="4Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="4Gi",
                mem_lim_recommended="1Gi",
            ),
        ]

        self._setup_mock_query_chain(
            mock_dal, scan_meta_data, scan_results_data, sort_by="memory_total"
        )

        # Call with sort_by memory_total
        results = mock_dal.get_resource_recommendation(sort_by="memory_total")

        assert results is not None
        assert len(results) == 2
        # high-memory should be first (higher memory savings)
        assert results[0]["name"] == "high-memory"
        assert results[1]["name"] == "low-memory"

    def test_sort_by_priority(self, mock_dal):
        """Test sorting by priority field."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="low-priority",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
                priority=3,
            ),
            self._create_mock_scan_result(
                name="high-priority",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="100m",
                cpu_req_recommended="50m",
                cpu_lim_allocated="100m",
                cpu_lim_recommended="50m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
                priority=10,
            ),
        ]

        # For priority sorting, we need to mock the order() call
        meta_query, results_query = self._setup_mock_query_chain(
            mock_dal, scan_meta_data, scan_results_data, sort_by="priority"
        )

        # Mock order to return results sorted by priority (descending)
        sorted_data = sorted(
            scan_results_data, key=lambda x: x["priority"], reverse=True
        )
        results_query.execute.return_value.data = sorted_data

        # Call with sort_by priority
        results = mock_dal.get_resource_recommendation(sort_by="priority", limit=2)

        assert results is not None
        assert len(results) == 2
        # Results should already be sorted by priority descending
        assert results[0]["name"] == "high-priority"
        assert results[1]["name"] == "low-priority"

    def test_filter_by_namespace(self, mock_dal):
        """Test filtering by namespace."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="app-prod",
                namespace="production",
                kind="Deployment",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
            ),
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call with namespace filter
        results = mock_dal.get_resource_recommendation(namespace="production")

        assert results is not None
        assert len(results) == 1
        assert results[0]["namespace"] == "production"

    def test_filter_by_name_pattern(self, mock_dal):
        """Test filtering by name pattern."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="frontend-app",
                namespace="default",
                kind="Deployment",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
            ),
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call with name_pattern filter
        results = mock_dal.get_resource_recommendation(name_pattern="frontend%")

        assert results is not None
        assert len(results) == 1
        assert results[0]["name"] == "frontend-app"

    def test_filter_by_kind(self, mock_dal):
        """Test filtering by kind."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="my-statefulset",
                namespace="default",
                kind="StatefulSet",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
            ),
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call with kind filter
        results = mock_dal.get_resource_recommendation(kind="StatefulSet")

        assert results is not None
        assert len(results) == 1
        assert results[0]["kind"] == "StatefulSet"

    def test_filter_by_container(self, mock_dal):
        """Test filtering by container name."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="my-app",
                namespace="default",
                kind="Deployment",
                container="sidecar",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
            ),
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call with container filter
        results = mock_dal.get_resource_recommendation(container="sidecar")

        assert results is not None
        assert len(results) == 1
        assert results[0]["container"] == "sidecar"

    def test_no_scan_metadata(self, mock_dal):
        """Test when no scan metadata is found."""
        scan_meta_data = []  # Empty scan metadata

        self._setup_mock_query_chain(mock_dal, scan_meta_data, [])

        # Call method
        results = mock_dal.get_resource_recommendation()

        assert results is None

    def test_no_scan_results(self, mock_dal):
        """Test when scan metadata exists but no results."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = []  # Empty results

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call method
        results = mock_dal.get_resource_recommendation()

        assert results is None

    def test_dal_disabled(self, mock_dal):
        """Test when DAL is disabled."""
        mock_dal.enabled = False

        # Call method
        results = mock_dal.get_resource_recommendation()

        assert results == []

    def test_multiple_filters_combined(self, mock_dal):
        """Test combining multiple filters."""
        scan_meta_data = [{"cluster_id": "test-cluster", "scan_id": "scan-123"}]
        scan_results_data = [
            self._create_mock_scan_result(
                name="prod-frontend",
                namespace="production",
                kind="Deployment",
                container="main",
                cpu_req_allocated="1000m",
                cpu_req_recommended="500m",
                cpu_lim_allocated="1000m",
                cpu_lim_recommended="500m",
                mem_req_allocated="1Gi",
                mem_req_recommended="1Gi",
                mem_lim_allocated="1Gi",
                mem_lim_recommended="1Gi",
            ),
        ]

        self._setup_mock_query_chain(mock_dal, scan_meta_data, scan_results_data)

        # Call with multiple filters
        results = mock_dal.get_resource_recommendation(
            namespace="production",
            name_pattern="prod%",
            kind="Deployment",
            container="main",
        )

        assert results is not None
        assert len(results) == 1
        assert results[0]["name"] == "prod-frontend"
        assert results[0]["namespace"] == "production"
        assert results[0]["kind"] == "Deployment"
        assert results[0]["container"] == "main"
