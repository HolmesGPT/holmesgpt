from typing import Dict, Optional

import pytest

from holmes.plugins.toolsets.grafana.common import build_headers


@pytest.mark.parametrize(
    "api_key, additional_headers, expected_headers",
    [
        (
            None,
            None,
            {"Accept": "application/json", "Content-Type": "application/json"},
        ),
        (
            "test_api_key_123",
            None,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_123",
            },
        ),
        (
            None,
            {"X-Request-ID": "req-abc"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Request-ID": "req-abc",
            },
        ),
        (
            "test_api_key_456",
            {"X-Custom-Header": "custom-value"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_456",
                "X-Custom-Header": "custom-value",
            },
        ),
        (
            None,
            {"Accept": "application/xml"},
            {"Accept": "application/xml", "Content-Type": "application/json"},
        ),
        (
            "test_api_key_789",
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Basic dXNlcjpwYXNz",
            },
        ),
        (
            "test_api_key_101",
            {},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_101",
            },
        ),
        (None, {}, {"Accept": "application/json", "Content-Type": "application/json"}),
    ],
)
def test_build_headers(
    api_key: Optional[str],
    additional_headers: Optional[Dict[str, str]],
    expected_headers: Dict[str, str],
):
    """Tests the build_headers function with various inputs."""
    result_headers = build_headers(api_key, additional_headers)
    assert result_headers == expected_headers
