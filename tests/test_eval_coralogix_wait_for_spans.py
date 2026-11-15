import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterator, List

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tests/llm/fixtures/shared/coralogix/wait_for_spans.sh"
EVAL_174 = REPO_ROOT / "tests/llm/fixtures/test_ask_holmes/174_coralogix_traces_ad"
API_KEY = "cx-test-key-0123456789"
QUERY = "app-174 AND ad-service"


@dataclass
class Reply:
    status: int
    body: str = ""
    delay_seconds: float = 0


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: Dict[str, str]
    body: dict


@dataclass
class FakeDataPrime:
    """Answers with `replies` in order, repeating the last one once they run out."""

    replies: List[Reply]
    requests: List[RecordedRequest] = field(default_factory=list)
    url: str = ""


def span_row(trace_id: str) -> dict:
    # A child span names its trace twice (top level and in references), like real
    # Coralogix spans do.
    user_data = {
        "traceID": trace_id,
        "spanID": "ae16a6aae11ababe",
        "operationName": "database_query",
        "references": [{"refType": "CHILD_OF", "traceID": trace_id}],
    }
    return {"metadata": [], "labels": [], "userData": json.dumps(user_data)}


def dataprime_body(span_count: int) -> str:
    lines = [{"queryId": {"queryId": "0689c46d"}}]
    if span_count:
        rows = [span_row(f"{i:032x}") for i in range(span_count)]
        lines.append({"result": {"results": rows}})
    lines.append(
        {"statistics": {"status": "COMPLETED", "outputRowCount": str(span_count)}}
    )
    return "\n".join(json.dumps(line) for line in lines) + "\n"


@pytest.fixture
def dataprime() -> Iterator[FakeDataPrime]:
    fake = FakeDataPrime(replies=[Reply(200, dataprime_body(0))])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            fake.requests.append(
                RecordedRequest(
                    method=self.command,
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=json.loads(self.rfile.read(length)),
                )
            )
            index = min(len(fake.requests), len(fake.replies)) - 1
            reply = fake.replies[index]
            time.sleep(reply.delay_seconds)
            payload = reply.body.encode()
            try:
                self.send_response(reply.status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}/api/v1/dataprime/query"
    yield fake
    server.shutdown()
    server.server_close()


def run_script(
    url: str, *args: str, timeout: float = 30, **env_overrides: str
) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")
    }
    env.update(
        {
            "NO_PROXY": "127.0.0.1,localhost",
            "CORALOGIX_API_KEY": API_KEY,
            "CORALOGIX_DATAPRIME_URL": url,
            "INTERVAL_SECONDS": "1",
            "ATTEMPTS": "5",
            "CURL_MAX_TIME": "5",
        }
    )
    env.update(env_overrides)
    if "CORALOGIX_API_KEY" in env_overrides and not env_overrides["CORALOGIX_API_KEY"]:
        del env["CORALOGIX_API_KEY"]
    return subprocess.run(
        ["bash", str(SCRIPT), *(args or (QUERY,))],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_succeeds_when_enough_spans_on_first_attempt(dataprime):
    dataprime.replies = [Reply(200, dataprime_body(10))]

    result = run_script(dataprime.url)

    assert result.returncode == 0, result.stdout
    assert "✅ Found 10 spans in Coralogix" in result.stdout
    assert len(dataprime.requests) == 1


def test_keeps_waiting_through_empty_200s_then_succeeds(dataprime):
    dataprime.replies = [
        Reply(200, dataprime_body(0)),
        Reply(200, dataprime_body(3)),
        Reply(200, dataprime_body(12)),
    ]

    result = run_script(dataprime.url)

    assert result.returncode == 0, result.stdout
    assert "⏳ Attempt 1/5: HTTP 200, spans found=0" in result.stdout
    assert "⏳ Attempt 2/5: HTTP 200, spans found=3" in result.stdout
    assert "✅ Found 12 spans in Coralogix" in result.stdout
    assert len(dataprime.requests) == 3


@pytest.mark.parametrize("span_count, succeeds", [(9, False), (10, True)])
def test_min_spans_threshold_is_inclusive(dataprime, span_count, succeeds):
    dataprime.replies = [Reply(200, dataprime_body(span_count))]

    result = run_script(dataprime.url, ATTEMPTS="1")

    assert (result.returncode == 0) is succeeds, result.stdout


def test_counts_spans_not_trace_id_mentions(dataprime):
    # 5 child spans mention a trace ID 10 times; that is still only 5 spans.
    dataprime.replies = [Reply(200, dataprime_body(5))]

    result = run_script(dataprime.url, ATTEMPTS="1")

    assert result.returncode == 1, result.stdout
    assert "spans found=5" in result.stdout


@pytest.mark.parametrize(
    "status, body",
    [
        (400, "Compilation errors:\n - source does not exist at [0:7-0:13]: nosuch"),
        (401, '{"error": "unauthorized"}'),
        (403, ""),
        (429, '{"error": "Too many requests"}'),
        (500, "internal error"),
        (503, "upstream unavailable"),
    ],
)
def test_non_200_fails_on_first_attempt_with_status_and_body(dataprime, status, body):
    dataprime.replies = [Reply(status, body)]

    started = time.monotonic()
    result = run_script(dataprime.url)

    assert result.returncode == 1, result.stdout
    assert (
        f"❌ Coralogix DataPrime query failed on attempt 1/5: HTTP {status}, curl exit 0"
        in result.stdout
    )
    if body:
        assert body in result.stdout
    else:
        assert "Response body: (empty)" in result.stdout
    assert len(dataprime.requests) == 1
    assert time.monotonic() - started < 1


def test_error_after_empty_200s_reports_the_failing_attempt(dataprime):
    dataprime.replies = [Reply(200, dataprime_body(0)), Reply(429, "slow down")]

    result = run_script(dataprime.url)

    assert result.returncode == 1, result.stdout
    assert "failed on attempt 2/5: HTTP 429" in result.stdout
    assert len(dataprime.requests) == 2


def test_long_error_body_is_truncated(dataprime):
    dataprime.replies = [Reply(500, "x" * 5000)]

    result = run_script(dataprime.url)

    assert result.returncode == 1
    assert "x" * 2000 in result.stdout
    assert "x" * 2001 not in result.stdout


def test_connection_refused_fails_with_curl_exit_code():
    result = run_script("http://127.0.0.1:9/api/v1/dataprime/query")

    assert result.returncode == 1, result.stdout
    assert "failed on attempt 1/5: HTTP 000, curl exit 7" in result.stdout
    assert "Failed to connect" in result.stderr


def test_slow_response_hits_curl_max_time(dataprime):
    dataprime.replies = [Reply(200, dataprime_body(10), delay_seconds=3)]

    result = run_script(dataprime.url, CURL_MAX_TIME="1")

    assert result.returncode == 1, result.stdout
    assert "failed on attempt 1/5: HTTP 000, curl exit 28" in result.stdout


def test_gives_up_after_all_attempts_and_prints_last_body(dataprime):
    dataprime.replies = [Reply(200, dataprime_body(2))]

    started = time.monotonic()
    result = run_script(dataprime.url, ATTEMPTS="3")
    elapsed = time.monotonic() - started

    assert result.returncode == 1, result.stdout
    assert (
        "❌ Coralogix answered every query with HTTP 200 but fewer than 10 spans after 3 attempts"
        in result.stdout
    )
    assert 'Last response body (first 2000 bytes):\n{"queryId"' in result.stdout
    assert len(dataprime.requests) == 3
    # Sleeps between attempts only, not after the last one.
    assert 2 <= elapsed < 3


def test_sends_authenticated_dataprime_query(dataprime):
    dataprime.replies = [Reply(200, dataprime_body(10))]

    run_script(dataprime.url, MIN_SPANS="10")

    [request] = dataprime.requests
    assert request.method == "POST"
    assert request.path == "/api/v1/dataprime/query"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"] == "application/json"
    assert request.body == {
        "query": "source spans | lucene 'app-174 AND ad-service' | limit 10"
    }


def test_api_key_is_never_printed(dataprime):
    dataprime.replies = [Reply(403, "")]

    result = run_script(dataprime.url)

    assert API_KEY not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "args, env, message",
    [
        ((QUERY,), {"CORALOGIX_API_KEY": ""}, "Missing env var: CORALOGIX_API_KEY"),
        (("",), {}, "Usage: wait_for_spans.sh '<lucene query>'"),
        (("app-174 AND 'x'",), {}, "must not contain quotes or backslashes"),
        (('app-174 AND "x"',), {}, "must not contain quotes or backslashes"),
        (("app-174\\",), {}, "must not contain quotes or backslashes"),
        ((QUERY,), {"MIN_SPANS": "0"}, "MIN_SPANS must be a positive integer"),
        ((QUERY,), {"ATTEMPTS": "abc"}, "ATTEMPTS must be a positive integer"),
        ((QUERY,), {"INTERVAL_SECONDS": "-1"}, "INTERVAL_SECONDS must be a positive"),
        ((QUERY,), {"CURL_MAX_TIME": "1.5"}, "CURL_MAX_TIME must be a positive"),
    ],
)
def test_invalid_input_fails_before_querying(dataprime, args, env, message):
    result = run_script(dataprime.url, *args, **env)

    assert result.returncode == 1, result.stdout
    assert message in result.stdout
    assert dataprime.requests == []


def test_eval_174_setup_uses_the_script():
    before_test = yaml.safe_load((EVAL_174 / "test_case.yaml").read_text())[
        "before_test"
    ]

    assert (
        "bash ../../shared/coralogix/wait_for_spans.sh 'app-174 AND ad-service' || exit 1"
        in before_test
    )
    assert (EVAL_174 / "../../shared/coralogix/wait_for_spans.sh").resolve() == SCRIPT
