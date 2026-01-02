"""Unit tests for KRR utilities."""

import pytest

from holmes.utils.krr_utils import calculate_krr_savings, parse_cpu, parse_memory


class TestParseCpu:
    """Tests for parse_cpu function."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            # Numeric values (already in cores)
            (0.5, 0.5),
            (1, 1.0),
            (2.5, 2.5),
            (0, 0.0),
            # Millicores string
            ("100m", 0.1),
            ("500m", 0.5),
            ("1000m", 1.0),
            ("1500m", 1.5),
            ("10m", 0.01),
            ("0m", 0.0),
            # String numeric
            ("0.5", 0.5),
            ("1", 1.0),
            ("2.5", 2.5),
            ("0", 0.0),
            # Edge cases with whitespace
            ("  100m  ", 0.1),
        ],
    )
    def test_parse_valid_values(self, input_value, expected):
        """Test parsing valid CPU values."""
        assert parse_cpu(input_value) == expected

    @pytest.mark.parametrize(
        "input_value",
        [
            None,
            "",
            "?",
            "invalid",
            "abc",
        ],
    )
    def test_parse_invalid_values(self, input_value):
        """Test parsing invalid CPU values returns 0.0."""
        assert parse_cpu(input_value) == 0.0


class TestParseMemory:
    """Tests for parse_memory function."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            # Numeric values (already in bytes)
            (1024, 1024.0),
            (1048576, 1048576.0),
            (0, 0.0),
            # Kibibytes
            ("100Ki", 100 * 1024),
            ("1Ki", 1024),
            # Mebibytes
            ("100Mi", 100 * 1024**2),
            ("1Mi", 1048576),
            # Gibibytes
            ("2Gi", 2 * 1024**3),
            ("1Gi", 1073741824),
            # Tebibytes
            ("1Ti", 1024**4),
            # Kilobytes
            ("100K", 100 * 1000),
            # Megabytes
            ("100M", 100 * 1000**2),
            # Gigabytes
            ("2G", 2 * 1000**3),
            # String numeric
            ("1048576", 1048576.0),
            ("0", 0.0),
            # Edge cases with whitespace
            ("  100Mi  ", 100 * 1024**2),
            ("0Mi", 0.0),
        ],
    )
    def test_parse_valid_values(self, input_value, expected):
        """Test parsing valid memory values."""
        assert parse_memory(input_value) == expected

    @pytest.mark.parametrize(
        "input_value",
        [
            None,
            "",
            "?",
            "invalid",
            "abc",
        ],
    )
    def test_parse_invalid_values(self, input_value):
        """Test parsing invalid memory values returns 0.0."""
        assert parse_memory(input_value) == 0.0


class TestCalculateKrrSavings:
    """Tests for calculate_krr_savings function."""

    @pytest.mark.parametrize(
        "sort_by,allocated_req,allocated_lim,recommended_req,recommended_lim,expected",
        [
            # CPU total with numeric values
            ("cpu_total", 2.0, 4.0, 0.5, 1.0, 4.5),  # (2.0-0.5) + (4.0-1.0) = 4.5
            # CPU total with millicores
            ("cpu_total", "2000m", 4.0, "500m", "1000m", 4.5),
            # CPU requests only
            ("cpu_requests", 2.0, 4.0, 0.5, 1.0, 1.5),  # 2.0-0.5 = 1.5 (limits ignored)
            # CPU limits only
            ("cpu_limits", 2.0, 4.0, 0.5, 1.0, 3.0),  # 4.0-1.0 = 3.0 (requests ignored)
        ],
    )
    def test_cpu_savings(
        self,
        sort_by,
        allocated_req,
        allocated_lim,
        recommended_req,
        recommended_lim,
        expected,
    ):
        """Test calculating CPU savings with different sort options."""
        result = {
            "content": [
                {
                    "resource": "cpu",
                    "allocated": {"request": allocated_req, "limit": allocated_lim},
                    "recommended": {
                        "request": recommended_req,
                        "limit": recommended_lim,
                    },
                }
            ]
        }
        assert calculate_krr_savings(result, sort_by) == expected

    @pytest.mark.parametrize(
        "sort_by,allocated_req,allocated_lim,recommended_req,recommended_lim,expected",
        [
            # Memory total with numeric values (bytes)
            (
                "memory_total",
                2147483648,
                4294967296,
                1073741824,
                2147483648,
                3 * 1024**3,
            ),
            # Memory total with unit strings
            ("memory_total", "2Gi", "4Gi", "1Gi", "2Gi", 3 * 1024**3),
            # Memory requests only
            ("memory_requests", "2Gi", "4Gi", "1Gi", "2Gi", 1024**3),
            # Memory limits only
            ("memory_limits", "2Gi", "4Gi", "1Gi", "2Gi", 2 * 1024**3),
        ],
    )
    def test_memory_savings(
        self,
        sort_by,
        allocated_req,
        allocated_lim,
        recommended_req,
        recommended_lim,
        expected,
    ):
        """Test calculating memory savings with different sort options."""
        result = {
            "content": [
                {
                    "resource": "memory",
                    "allocated": {"request": allocated_req, "limit": allocated_lim},
                    "recommended": {
                        "request": recommended_req,
                        "limit": recommended_lim,
                    },
                }
            ]
        }
        assert calculate_krr_savings(result, sort_by) == expected

    @pytest.mark.parametrize(
        "sort_by,cpu_alloc,cpu_rec,mem_alloc,mem_rec,expected",
        [
            # Sort by CPU - should only use CPU data
            (
                "cpu_total",
                {"request": 2.0, "limit": 4.0},
                {"request": 0.5, "limit": 1.0},
                {"request": "4Gi", "limit": "8Gi"},
                {"request": "1Gi", "limit": "2Gi"},
                4.5,  # (2.0-0.5) + (4.0-1.0)
            ),
            # Sort by memory - should only use memory data
            (
                "memory_total",
                {"request": 2.0, "limit": 4.0},
                {"request": 0.5, "limit": 1.0},
                {"request": "4Gi", "limit": "8Gi"},
                {"request": "1Gi", "limit": "2Gi"},
                9 * 1024**3,  # (4Gi-1Gi) + (8Gi-2Gi)
            ),
        ],
    )
    def test_multiple_resources(
        self, sort_by, cpu_alloc, cpu_rec, mem_alloc, mem_rec, expected
    ):
        """Test with both CPU and memory data, ensuring correct resource is used."""
        result = {
            "content": [
                {"resource": "cpu", "allocated": cpu_alloc, "recommended": cpu_rec},
                {"resource": "memory", "allocated": mem_alloc, "recommended": mem_rec},
            ]
        }
        assert calculate_krr_savings(result, sort_by) == expected

    def test_negative_savings_when_recommended_higher(self):
        """Test negative savings"""
        result = {
            "content": [
                {
                    "resource": "cpu",
                    "allocated": {"request": 0.5, "limit": 1.0},
                    "recommended": {"request": 2.0, "limit": 4.0},
                }
            ]
        }
        assert calculate_krr_savings(result, "cpu_total") == -4.5

    @pytest.mark.parametrize(
        "allocated,recommended,expected",
        [
            # None values
            ({"request": 2.0, "limit": None}, {"request": 0.5, "limit": None}, 1.5),
            # Question mark values
            ({"request": 2.0, "limit": "?"}, {"request": "?", "limit": "?"}, 2.0),
        ],
    )
    def test_handles_special_values(self, allocated, recommended, expected):
        """Test handling None and '?' string values."""
        result = {
            "content": [
                {"resource": "cpu", "allocated": allocated, "recommended": recommended}
            ]
        }
        assert calculate_krr_savings(result, "cpu_total") == expected

    @pytest.mark.parametrize(
        "result,expected",
        [
            # Empty content
            ({"content": []}, 0.0),
            # No content field
            ({}, 0.0),
            # Content not a list
            ({"content": "not a list"}, 0.0),
            # Missing resource type
            (
                {
                    "content": [
                        {
                            "allocated": {"request": 2.0, "limit": 4.0},
                            "recommended": {"request": 0.5, "limit": 1.0},
                        }
                    ]
                },
                0.0,
            ),
        ],
    )
    def test_edge_cases(self, result, expected):
        """Test edge cases with invalid or missing data."""
        assert calculate_krr_savings(result, "cpu_total") == expected

    def test_real_world_data_structure(self):
        """Test with real-world KRR data structure from the provided example."""
        result = {
            "account_id": "16ecba1a-7993-4dd1-a98c-d201462ccba7",
            "cluster_id": "nicolas-demo-prod-double-node",
            "scan_id": "d3340393-b22c-4d90-a82d-fc82c5597680",
            "scan_type": "krr",
            "namespace": "default",
            "name": "robusta-forwarder",
            "kind": "Deployment",
            "container": "kubewatch",
            "priority": 3,
            "content": [
                {
                    "info": None,
                    "metric": {},
                    "priority": {"limit": 1, "request": 1},
                    "resource": "cpu",
                    "allocated": {"limit": None, "request": 0.01},
                    "recommended": {"limit": None, "request": 0.01},
                },
                {
                    "info": None,
                    "metric": {},
                    "priority": {"limit": 3, "request": 3},
                    "resource": "memory",
                    "allocated": {"limit": 536870912, "request": 536870912},
                    "recommended": {"limit": 104857600, "request": 104857600},
                },
            ],
        }
        # CPU savings: (0.01 - 0.01) + (0 - 0) = 0
        assert calculate_krr_savings(result, "cpu_total") == 0.0

        # Memory savings: (536870912 - 104857600) + (536870912 - 104857600) = 864026624
        expected_memory_savings = (536870912 - 104857600) * 2
        assert calculate_krr_savings(result, "memory_total") == expected_memory_savings
