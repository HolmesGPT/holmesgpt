"""
This is a set of integration tests intended to be run manually
Change the TEST_** variables defined below based on the content in opensearch to validate that the implementation is working as expected
"""

import pytest
import os

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams
from holmes.plugins.toolsets.opensearch.opensearch_logs import (
    OpenSearchLogsToolset,
)
from holmes.plugins.toolsets.opensearch.opensearch_utils import (
    OpenSearchLoggingConfig,
    opensearch_health_check,
)

REQUIRED_ENV_VARS = [
    "TEST_OPENSEARCH_URL",
    "TEST_OPENSEARCH_INDEX",
    "TEST_OPENSEARCH_AUTH_HEADER",
]


TEST_NAMESPACE = "default"
TEST_POD_NAME = "robusta-holmes-5c85f89f64-bccp8"
TEST_SEARCH_TERM = "10.244.1.146"

# the date range below combined with the search term is expected to return a single log line
TEST_START_TIME = "2025-05-05T13:20:00Z"
TEST_END_TIME = "2025-05-05T13:21:00Z"


# Check if any required environment variables are missing
missing_vars = [var for var in REQUIRED_ENV_VARS if os.environ.get(var) is None]

# Use pytest.mark.skip (not skipif) to show a single grouped skip line for the entire module
# Will show: "SKIPPED [4] module.py: reason" instead of 4 separate skip lines
if missing_vars:
    pytestmark = pytest.mark.skip(reason=f"{', '.join(missing_vars)} must be set")


@pytest.fixture
def opensearch_config() -> OpenSearchLoggingConfig:
    """Create config - env vars guaranteed to be present due to module-level skip check"""

    return OpenSearchLoggingConfig(
        opensearch_url=os.environ["TEST_OPENSEARCH_URL"],
        index_pattern=os.environ["TEST_OPENSEARCH_INDEX"],
        opensearch_auth_header=os.environ["TEST_OPENSEARCH_AUTH_HEADER"],
    )


@pytest.fixture
def opensearch_logs_toolset(opensearch_config) -> OpenSearchLogsToolset:
    """Create an OpenSearchLogsToolset with the test configuration"""
    toolset = OpenSearchLogsToolset()
    ok, reason = toolset.prerequisites_callable(opensearch_config.model_dump())
    assert ok, reason
    return toolset


def test_health_check(opensearch_config):
    ok, reason = opensearch_health_check(opensearch_config)
    assert ok, reason


def test_basic_query(opensearch_logs_toolset):
    result = opensearch_logs_toolset.fetch_pod_logs(
        FetchPodLogsParams(namespace=TEST_NAMESPACE, pod_name=TEST_POD_NAME)
    )

    assert result.status == StructuredToolResultStatus.SUCCESS, result.error
    assert not result.error
    print(result.data)
    assert TEST_SEARCH_TERM in result.data


def test_search_term(opensearch_logs_toolset):
    result = opensearch_logs_toolset.fetch_pod_logs(
        FetchPodLogsParams(
            namespace=TEST_NAMESPACE, pod_name=TEST_POD_NAME, filter=TEST_SEARCH_TERM
        )
    )

    assert result.status == StructuredToolResultStatus.SUCCESS, result.error
    assert not result.error
    print(result.data)
    for line in result.data.split("\n"):
        assert TEST_SEARCH_TERM in line, line


def test_search_term_with_dates(opensearch_logs_toolset):
    result = opensearch_logs_toolset.fetch_pod_logs(
        FetchPodLogsParams(
            namespace=TEST_NAMESPACE,
            pod_name=TEST_POD_NAME,
            filter=TEST_SEARCH_TERM,
            start_time=TEST_START_TIME,
            end_time=TEST_END_TIME,
        )
    )

    assert result.status == StructuredToolResultStatus.SUCCESS, result.error
    assert not result.error
    print(result.data)

    assert TEST_SEARCH_TERM in result.data
    assert len(result.data.split("\n")) == 1
