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

Mock Strategy:
We mock at the litellm.completion level rather than internal Holmes classes.
This ensures we test the full code path from HTTP request through to LLM call,
making tests resilient to internal refactoring while still avoiding real API calls.

We also mock the model registry to return a non-Robusta model, avoiding the need
for Robusta AI credentials during tests.
"""

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import ModelEntry
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


def format_doc_test_failure(
    test_id: str,
    doc_test: DocCurlTest,
    error_type: str,
    expected: Any,
    actual: Any,
    extra_info: str = "",
) -> str:
    """
    Format a clear failure message for documentation tests.

    These tests validate that curl examples in our docs actually work.
    If this fails, the documentation has an incorrect example that needs fixing.
    """
    curl_preview = doc_test.raw_command[:300]
    if len(doc_test.raw_command) > 300:
        curl_preview += "..."

    # Get relative path for cleaner output
    source_file = doc_test.curl.source_file
    try:
        source_file = str(Path(source_file).relative_to(Path.cwd()))
    except ValueError:
        pass

    lines = [
        "",
        "DOCUMENTATION CURL TEST FAILED",
        "",
        f"This test checks that curl examples in docs work correctly.",
        f"A curl example in the documentation returned an unexpected result.",
        "",
        f"Test ID: {test_id}",
        f"Source:  {source_file}:{doc_test.curl.line_number}",
        "",
        f"Error:    {error_type}",
        f"Expected: {expected}",
        f"Actual:   {actual}",
    ]

    if extra_info:
        lines.append(f"Details:  {extra_info}")

    lines.extend([
        "",
        f"Curl: {curl_preview}",
        "",
        "To fix: Edit the curl example or its <!-- test: ... --> annotation",
    ])

    return "\n".join(lines)


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


def create_mock_model_entry() -> ModelEntry:
    """
    Create a mock ModelEntry for a non-Robusta model.

    This avoids the Robusta AI credential check during tests while still
    testing the full LLM code path.
    """
    return ModelEntry(
        name="test-model",
        model="gpt-4o",
        is_robusta_model=False,
    )


def create_mock_litellm_response(content: str = "Mock analysis response for documentation test.") -> ModelResponse:
    """
    Create a mock litellm ModelResponse matching the real API structure.

    This is what litellm.completion() returns. By mocking at this level,
    we test the full Holmes code path from HTTP to LLM integration.
    """
    return ModelResponse(
        id="chatcmpl-mock-doc-test",
        choices=[
            Choices(
                index=0,
                message=Message(
                    role="assistant",
                    content=content,
                    tool_calls=None,
                ),
                finish_reason="stop",
            )
        ],
        model="gpt-4o-mock",
        usage=Usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
    )


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
@patch("litellm.completion")
@patch("holmes.core.llm.LLMModelRegistry.get_model_params")
@patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
@patch.dict("os.environ", {"OPENAI_API_KEY": "test-api-key-for-docs"})
def test_documented_curl(
    mock_get_global_instructions,
    mock_get_model_params,
    mock_litellm_completion,
    test_id: str,
    doc_test: DocCurlTest,
    client,
):
    """
    Test a curl command extracted from documentation.

    This test validates that documented curl examples actually work
    and return expected responses. We mock at the litellm.completion level
    to test the full Holmes code path while avoiding real API calls.

    Mock strategy:
    - LLMModelRegistry.get_model_params: Returns a non-Robusta model to avoid credential checks
    - litellm.completion: Returns a mock response to avoid real API calls
    - SupabaseDal.get_global_instructions_for_account: Returns empty list
    """
    # Check if this is a complex endpoint that needs special handling
    endpoint = get_endpoint_from_url(doc_test.curl.url)
    if endpoint in COMPLEX_ENDPOINTS:
        pytest.skip(
            f"Endpoint {endpoint} requires complex mocking - "
            "tested separately in test_server_endpoints.py"
        )

    # Setup mocks
    # 1. Model registry returns a non-Robusta model (avoids credential checks)
    mock_get_model_params.return_value = create_mock_model_entry()
    # 2. litellm.completion returns a mock response (tests full code path)
    mock_litellm_completion.return_value = create_mock_litellm_response()
    # 3. Global instructions returns empty list
    mock_get_global_instructions.return_value = []

    # Execute the curl
    result = execute_curl_test(client, doc_test)

    # Validate status code
    if result["status_code"] != doc_test.curl.expected_status:
        pytest.fail(
            format_doc_test_failure(
                test_id=test_id,
                doc_test=doc_test,
                error_type="Wrong HTTP status code",
                expected=doc_test.curl.expected_status,
                actual=result["status_code"],
                extra_info=f"Response: {str(result.get('json', ''))[:100]}",
            )
        )

    # Validate expected fields in JSON response
    if doc_test.curl.expected_fields and result["json"]:
        for field in doc_test.curl.expected_fields:
            if field not in result["json"]:
                pytest.fail(
                    format_doc_test_failure(
                        test_id=test_id,
                        doc_test=doc_test,
                        error_type="Missing expected field in response",
                        expected=f"field '{field}' present",
                        actual=f"fields: {list(result['json'].keys())}",
                    )
                )


# Alternative: Test specific doc files explicitly
class TestHttpApiDocs:
    """Tests for http-api.md documentation."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, monkeypatch):
        """
        Setup common mocks for all tests.

        Mock strategy:
        - OPENAI_API_KEY env var: Set fake key to pass credential validation
        - LLMModelRegistry.get_model_params: Returns a non-Robusta model to avoid credential checks
        - litellm.completion: Returns a mock response to avoid real API calls
        - SupabaseDal.get_global_instructions_for_account: Returns empty list
        """
        # Set fake API key to pass credential validation
        monkeypatch.setenv("OPENAI_API_KEY", "test-api-key-for-docs")

        with patch("litellm.completion") as mock_completion, \
             patch("holmes.core.llm.LLMModelRegistry.get_model_params") as mock_model, \
             patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account") as mock_instr:

            mock_model.return_value = create_mock_model_entry()
            mock_completion.return_value = create_mock_litellm_response("Test response")
            mock_instr.return_value = []

            yield {"mock_completion": mock_completion, "mock_model": mock_model}

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
