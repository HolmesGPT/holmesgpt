"""Unit tests for SupabaseDal.get_resource_recommendation method."""

from unittest.mock import Mock, patch

import pytest

from holmes.core.supabase_dal import SupabaseDal


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
