"""
Documentation-driven HTTP endpoint tests.

This module automatically extracts curl commands from documentation and tests them.
The docs serve as the single source of truth - no duplicate test definitions needed.

To make a curl example testable, add a test annotation comment:
```bash
<!-- test: status=200, has_fields=analysis|tool_calls -->
curl -X POST http://<HOLMES-URL>/api/chat ...
```

Annotation options:
- status: expected HTTP status (default: 200)
- has_fields: pipe-separated list of expected JSON fields
- skip: skip this test (true/false)
- id: test identifier for -k filtering
- desc: test description
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server import app
from tests.utils.curl_parser import (
    DocCurlTest,
    extract_curl_tests_from_file,
    substitute_placeholders,
)

# Directory containing documentation
DOCS_DIR = Path(__file__).parent.parent / "docs"

# Placeholder substitutions for testing
PLACEHOLDER_SUBSTITUTIONS = {
    "<HOLMES-URL>": "testserver",
    "http://testserver": "",  # TestClient uses relative URLs
    "http://localhost:8080": "",
}


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def mock_llm():
    """Mock LLM for testing without real API calls."""
    mock_ai = MagicMock()
    mock_ai.messages_call.return_value = MagicMock(
        result="Mock analysis response for documentation test.",
        tool_calls=[
            {
                "tool_call_id": "doc_test_1",
                "tool_name": "mock_tool",
                "description": "Mock tool for testing",
                "result": {"status": "success", "data": "mock data"},
            }
        ],
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "Mock response"},
        ],
        metadata={},
    )
    return mock_ai


def collect_doc_curl_tests() -> list[tuple[str, DocCurlTest]]:
    """
    Collect all testable curl commands from documentation.

    Returns list of (test_id, DocCurlTest) tuples for pytest parametrization.
    """
    tests = []

    # Find all markdown files in docs directory
    if not DOCS_DIR.exists():
        return tests

    for md_file in DOCS_DIR.rglob("*.md"):
        doc_tests = extract_curl_tests_from_file(md_file)
        for doc_test in doc_tests:
            if doc_test.curl.skip:
                continue

            # Generate test ID
            relative_path = md_file.relative_to(DOCS_DIR)
            test_id = doc_test.curl.test_id or f"{relative_path}:{doc_test.curl.line_number}"
            tests.append((test_id, doc_test))

    return tests


# Collect tests at module load time for parametrization
DOC_CURL_TESTS = collect_doc_curl_tests()


def normalize_url(url: str) -> str:
    """Normalize URL for TestClient (remove host, keep path)."""
    # Apply substitutions
    for old, new in PLACEHOLDER_SUBSTITUTIONS.items():
        url = url.replace(old, new)

    # Extract just the path
    if url.startswith("http://") or url.startswith("https://"):
        # Remove protocol and host
        parts = url.split("/", 3)
        if len(parts) >= 4:
            url = "/" + parts[3]
        else:
            url = "/"

    # Ensure starts with /
    if not url.startswith("/"):
        url = "/" + url

    return url


def execute_curl_test(
    client: TestClient,
    doc_test: DocCurlTest,
    mock_ai: Any,
) -> dict[str, Any]:
    """Execute a curl command using TestClient and return result."""
    curl = substitute_placeholders(doc_test.curl, PLACEHOLDER_SUBSTITUTIONS)
    url = normalize_url(curl.url)
    method = curl.method.upper()

    # Build request kwargs
    kwargs: dict[str, Any] = {}

    if curl.headers:
        kwargs["headers"] = curl.headers

    if curl.json_data:
        kwargs["json"] = curl.json_data
    elif curl.data:
        kwargs["content"] = curl.data

    # Execute request
    if method == "GET":
        response = client.get(url, **kwargs)
    elif method == "POST":
        response = client.post(url, **kwargs)
    elif method == "PUT":
        response = client.put(url, **kwargs)
    elif method == "DELETE":
        response = client.delete(url, **kwargs)
    elif method == "PATCH":
        response = client.patch(url, **kwargs)
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")

    return {
        "status_code": response.status_code,
        "response": response,
        "json": response.json() if response.headers.get("content-type", "").startswith("application/json") else None,
    }


def get_endpoint_from_url(url: str) -> str:
    """Extract the endpoint path from a URL."""
    # Handle placeholders and normalize
    url = url.replace("<HOLMES-URL>", "localhost")
    if "://" in url:
        url = "/" + url.split("/", 3)[-1]
    return url.split("?")[0]  # Remove query params


# Endpoints that require complex mocking (investigation workflows)
COMPLEX_ENDPOINTS = {
    "/api/investigate",
    "/api/stream/investigate",
    "/api/workload_health_check",
    "/api/workload_health_chat",
}


@pytest.mark.skipif(
    len(DOC_CURL_TESTS) == 0,
    reason="No testable curl commands found in documentation",
)
@pytest.mark.parametrize(
    "test_id,doc_test",
    DOC_CURL_TESTS,
    ids=[t[0] for t in DOC_CURL_TESTS],
)
@patch("holmes.config.Config.create_toolcalling_llm")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
def test_documented_curl(
    mock_get_global_instructions,
    mock_create_toolcalling_llm,
    test_id: str,
    doc_test: DocCurlTest,
    client,
    mock_llm,
):
    """
    Test a curl command extracted from documentation.

    This test validates that documented curl examples actually work
    and return expected responses.
    """
    # Check if this is a complex endpoint that needs special handling
    endpoint = get_endpoint_from_url(doc_test.curl.url)
    if endpoint in COMPLEX_ENDPOINTS:
        pytest.skip(
            f"Endpoint {endpoint} requires complex mocking - "
            "tested separately in test_server_endpoints.py"
        )

    # Setup mocks
    mock_create_toolcalling_llm.return_value = mock_llm
    mock_get_global_instructions.return_value = []

    # Execute the curl
    result = execute_curl_test(client, doc_test, mock_llm)

    # Validate status code
    assert result["status_code"] == doc_test.curl.expected_status, (
        f"Expected status {doc_test.curl.expected_status}, "
        f"got {result['status_code']} for {doc_test.raw_command[:100]}..."
    )

    # Validate expected fields in JSON response
    if doc_test.curl.expected_fields and result["json"]:
        for field in doc_test.curl.expected_fields:
            assert field in result["json"], (
                f"Expected field '{field}' not found in response. "
                f"Available fields: {list(result['json'].keys())}"
            )


# Alternative: Test specific doc files explicitly
class TestHttpApiDocs:
    """Tests for http-api.md documentation."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Setup common mocks for all tests."""
        with patch("holmes.config.Config.create_toolcalling_llm") as mock_llm, \
             patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account") as mock_instr:

            mock_ai = MagicMock()
            mock_ai.messages_call.return_value = MagicMock(
                result="Test response",
                tool_calls=[],
                messages=[],
                metadata={},
            )
            mock_llm.return_value = mock_ai
            mock_instr.return_value = []

            yield {"mock_llm": mock_llm, "mock_ai": mock_ai}

    def test_chat_endpoint_documented(self, client):
        """Verify /api/chat endpoint works as documented."""
        response = client.post(
            "/api/chat",
            json={"ask": "What is the status of my cluster?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "analysis" in data

    def test_model_endpoint_documented(self, client):
        """Verify /api/model endpoint works as documented."""
        response = client.get("/api/model")
        assert response.status_code == 200
        data = response.json()
        assert "model_name" in data


if __name__ == "__main__":
    # Debug: print discovered tests
    print(f"Found {len(DOC_CURL_TESTS)} testable curl commands in documentation:")
    for test_id, doc_test in DOC_CURL_TESTS:
        print(f"  - {test_id}: {doc_test.curl.method} {doc_test.curl.url}")
