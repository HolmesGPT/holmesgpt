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
We mock at two levels:
1. LLMModelRegistry.get_model_params - returns non-Robusta model (server config
   is initialized at import time before env vars can be set)
2. litellm.completion - returns mock response (litellm uses httpx internally,
   which the responses library doesn't mock)

This tests the full code path: HTTP -> server -> config -> LLM class -> litellm mock
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

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


def create_mock_model_entry() -> ModelEntry:
    """Create a mock ModelEntry for a non-Robusta model."""
    return ModelEntry(
        name="test-model",
        model="gpt-4o",
        is_robusta_model=False,
    )


def create_mock_litellm_response(content: str = "Mock analysis response for documentation test.") -> ModelResponse:
    """Create a mock litellm ModelResponse matching the real API structure."""
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


def format_doc_test_failure(
    test_id: str,
    doc_test: DocCurlTest,
    error_type: str,
    expected: Any,
    actual: Any,
    extra_info: str = "",
) -> str:
    """Format a clear failure message for documentation tests."""
    curl_preview = doc_test.raw_command[:300]
    if len(doc_test.raw_command) > 300:
        curl_preview += "..."

    source_file = doc_test.curl.source_file
    try:
        source_file = str(Path(source_file).relative_to(Path.cwd()))
    except ValueError:
        pass

    lines = [
        "",
        "DOCUMENTATION CURL TEST FAILED",
        "",
        "This test checks that curl examples in docs work correctly.",
        "A curl example in the documentation returned an unexpected result.",
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


def collect_doc_curl_tests() -> list[tuple[str, DocCurlTest]]:
    """Collect all testable curl commands from documentation."""
    tests = []

    if not DOCS_DIR.exists():
        return tests

    for md_file in DOCS_DIR.rglob("*.md"):
        doc_tests = extract_curl_tests_from_file(md_file)
        for doc_test in doc_tests:
            if doc_test.curl.skip:
                continue

            relative_path = md_file.relative_to(DOCS_DIR)
            test_id = doc_test.curl.test_id or f"{relative_path}:{doc_test.curl.line_number}"
            tests.append((test_id, doc_test))

    return tests


DOC_CURL_TESTS = collect_doc_curl_tests()


def normalize_url(url: str) -> str:
    """Normalize URL for TestClient (remove host, keep path)."""
    for old, new in PLACEHOLDER_SUBSTITUTIONS.items():
        url = url.replace(old, new)

    if url.startswith("http://") or url.startswith("https://"):
        parts = url.split("/", 3)
        if len(parts) >= 4:
            url = "/" + parts[3]
        else:
            url = "/"

    if not url.startswith("/"):
        url = "/" + url

    return url


def execute_curl_test(client: TestClient, doc_test: DocCurlTest) -> dict[str, Any]:
    """Execute a curl command using TestClient and return result."""
    curl = substitute_placeholders(doc_test.curl, PLACEHOLDER_SUBSTITUTIONS)
    url = normalize_url(curl.url)
    method = curl.method.upper()

    kwargs: dict[str, Any] = {}
    if curl.headers:
        kwargs["headers"] = curl.headers
    if curl.json_data:
        kwargs["json"] = curl.json_data
    elif curl.data:
        kwargs["content"] = curl.data

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
@patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
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

    Validates that documented curl examples return expected responses.
    Tests the full path: HTTP -> server -> config -> LLM -> litellm mock
    """
    # Setup mocks
    mock_get_model_params.return_value = create_mock_model_entry()
    mock_litellm_completion.return_value = create_mock_litellm_response()
    mock_get_global_instructions.return_value = []

    result = execute_curl_test(client, doc_test)

    # Log response for debugging (visible in CI artifacts/extended logs)
    print(f"\n=== Test: {test_id} ===")
    print(f"Endpoint: {doc_test.curl.method} {doc_test.curl.url}")
    print(f"Status: {result['status_code']} (expected: {doc_test.curl.expected_status})")
    print(f"Response: {result.get('json', result.get('response', 'N/A'))}")

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

    # Validate expected fields
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


class TestHttpApiDocs:
    """Tests for http-api.md documentation."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, monkeypatch):
        """Setup mocks for all tests."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        with patch("litellm.completion") as mock_completion, \
             patch("holmes.core.llm.LLMModelRegistry.get_model_params") as mock_model, \
             patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account") as mock_instr:
            mock_model.return_value = create_mock_model_entry()
            mock_completion.return_value = create_mock_litellm_response()
            mock_instr.return_value = []
            yield

    def test_chat_endpoint_documented(self, client):
        """Verify /api/chat endpoint works as documented."""
        response = client.post("/api/chat", json={"ask": "What is the status?"})
        assert response.status_code == 200
        assert "analysis" in response.json()

    def test_model_endpoint_documented(self, client):
        """Verify /api/model endpoint works as documented."""
        response = client.get("/api/model")
        assert response.status_code == 200
        assert "model_name" in response.json()


if __name__ == "__main__":
    print(f"Found {len(DOC_CURL_TESTS)} testable curl commands:")
    for test_id, doc_test in DOC_CURL_TESTS:
        print(f"  - {test_id}: {doc_test.curl.method} {doc_test.curl.url}")
