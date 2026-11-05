"""Runner <-> Holmes API contract.

The Robusta runner calls these Holmes endpoints (robusta repo,
src/robusta/core/playbooks/internal/ai_integration.py and
src/robusta/integrations/prometheus/utils.py):

    POST /api/chat            sync (ChatResponse JSON) and stream (SSE events)
    GET  /api/model           {"model_name": "<JSON-encoded list>"}
    POST /api/oauth/callback  OAuthCallbackRequest -> OAuthCallbackResponse

Holmes releases ship without runner testing, so this test is the guard:

1. Invariants: what the runner sends must stay accepted and what it reads
   must stay present. Failing these means a runner-breaking change.
2. Snapshot: the full request/response JSON schemas, committed in
   tests/fixtures/runner_api_contract.json. Any change fails until the
   snapshot is regenerated, so every contract change is reviewed on purpose:

       UPDATE_RUNNER_API_CONTRACT=1 poetry run pytest tests/test_runner_api_contract.py --no-cov
"""

import ast
import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from holmes.core.models import (
    ChatRequest,
    ChatResponse,
    OAuthCallbackRequest,
    OAuthCallbackResponse,
)
from holmes.utils.stream import StreamEvents

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = REPO_ROOT / "server.py"
SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "runner_api_contract.json"
UPDATE_ENV = "UPDATE_RUNNER_API_CONTRACT"

RUNNER_ENDPOINTS = {
    ("post", "/api/chat"): {"request": "ChatRequest"},
    ("get", "/api/model"): {"request": None},
    ("post", "/api/oauth/callback"): {
        "request": "OAuthCallbackRequest",
        "response": "OAuthCallbackResponse",
    },
}

# Fields of the runner's HolmesChatRequest (robusta repo,
# src/robusta/core/reporting/holmes.py). It also forwards unknown extras.
RUNNER_CHAT_REQUEST_FIELDS = {
    "ask",
    "conversation_history",
    "model",
    "stream",
    "enable_tool_approval",
    "tool_decisions",
    "additional_system_prompt",
    "request_type",
    "request_source",
    "user_email",
    "source_ref",
    "conversation_id",
    "conversation_source",
    "conversation_link",
    "is_internal",
    "meta",
}
# The runner's ToolApprovalDecision.
RUNNER_TOOL_DECISION_FIELDS = {"tool_call_id", "approved", "save_prefixes", "decision"}
# Fields the runner's HolmesChatResult / ToolCallResult read from ChatResponse.
RUNNER_CHAT_RESPONSE_FIELDS = {"analysis", "tool_calls", "conversation_history"}
RUNNER_TOOL_CALL_FIELDS = {"tool_name", "description", "result"}
# The runner forwards the UI's OAuth params verbatim (extra=allow).
RUNNER_OAUTH_FIELDS = {
    "toolset_name",
    "code",
    "code_verifier",
    "redirect_uri",
    "client_id",
    "client_secret",
    "resource",
}
# SSE event names the runner relays to the UI unchanged; the UI parses them.
RUNNER_STREAM_EVENTS = {
    "ai_answer_end",
    "start_tool_calling",
    "tool_calling_result",
    "error",
    "ai_message",
    "approval_required",
    "token_count",
}


def _fail(message: str) -> None:
    pytest.fail(
        "Runner <-> Holmes API contract broken: "
        + message
        + "\nThe Robusta runner calls this endpoint; changing it breaks runner "
        "installs that are not upgraded together with Holmes. Keep the change "
        "backward compatible (additive, optional fields).",
        pytrace=False,
    )


def _server_routes() -> Dict[tuple, Dict[str, Any]]:
    """(method, path) -> handler's request/response annotation names, read
    from server.py's source so the test does not start the server."""
    routes: Dict[tuple, Dict[str, Any]] = {}
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                continue
            params = [
                ast.unparse(a.annotation)
                for a in node.args.args
                if a.annotation is not None and ast.unparse(a.annotation) != "Request"
            ]
            dict_keys = sorted(
                k.value
                for ret in ast.walk(node)
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict)
                for k in ret.value.keys
                if isinstance(k, ast.Constant)
            )
            routes[(dec.func.attr, dec.args[0].value)] = {
                "request": params[0] if params else None,
                "response": ast.unparse(node.returns) if node.returns else None,
                "returned_dict_keys": dict_keys,
            }
    return routes


def _current_contract() -> Dict[str, Any]:
    routes = _server_routes()
    return {
        "endpoints": {
            f"{method.upper()} {path}": routes.get((method, path))
            for method, path in sorted(RUNNER_ENDPOINTS)
        },
        "schemas": {
            "ChatRequest": ChatRequest.model_json_schema(),
            "ChatResponse": ChatResponse.model_json_schema(),
            "OAuthCallbackRequest": OAuthCallbackRequest.model_json_schema(),
            "OAuthCallbackResponse": OAuthCallbackResponse.model_json_schema(),
        },
        "stream_events": sorted(e.value for e in StreamEvents),
    }


def _resolve(schema: Dict[str, Any], root: Dict[str, Any]) -> Dict[str, Any]:
    ref = schema.get("$ref")
    if ref:
        return root["$defs"][ref.split("/")[-1]]
    for option in schema.get("anyOf", []):
        if "$ref" in option or option.get("type") == "array":
            return _resolve(option, root)
    if schema.get("type") == "array" and "items" in schema:
        return _resolve(schema["items"], root)
    return schema


# ---- 1. invariants ----


def test_runner_endpoints_exist_with_expected_models():
    routes = _server_routes()
    for (method, path), expected in RUNNER_ENDPOINTS.items():
        route = routes.get((method, path))
        if route is None:
            _fail(f"{method.upper()} {path} is no longer defined in server.py")
        for key, value in expected.items():
            if route[key] != value:
                _fail(
                    f"{method.upper()} {path} {key} model changed: "
                    f"expected {value}, found {route[key]}"
                )
    if "model_name" not in routes[("get", "/api/model")]["returned_dict_keys"]:
        _fail("GET /api/model no longer returns a 'model_name' key")


def test_chat_request_accepts_what_the_runner_sends():
    schema = ChatRequest.model_json_schema()
    properties = set(schema["properties"])
    missing = RUNNER_CHAT_REQUEST_FIELDS - properties
    if missing:
        _fail(f"ChatRequest dropped runner-sent fields {sorted(missing)}")
    required = set(schema.get("required", []))
    if required - {"ask"}:
        _fail(
            f"ChatRequest requires {sorted(required - {'ask'})}; the runner only "
            "always sends 'ask'"
        )
    # Unknown extras the runner forwards must not be rejected.
    if ChatRequest.model_config.get("extra") == "forbid":
        _fail("ChatRequest forbids extra fields; the runner forwards extras")
    ChatRequest(
        ask="why is my pod crashing?",
        conversation_history=[{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
        stream=True,
        enable_tool_approval=True,
        tool_decisions=[{"tool_call_id": "t1", "approved": True, "decision": {"code": "x"}}],
        conversation_id="c1",
        is_internal=False,
        meta={"k": "v"},
    )

    decision = _resolve(schema["properties"]["tool_decisions"], schema)
    missing = RUNNER_TOOL_DECISION_FIELDS - set(decision["properties"])
    if missing:
        _fail(f"ToolApprovalDecision dropped runner-sent fields {sorted(missing)}")
    if set(decision.get("required", [])) - {"tool_call_id", "approved"}:
        _fail("ToolApprovalDecision requires fields the runner does not send")


def test_chat_response_has_what_the_runner_reads():
    schema = ChatResponse.model_json_schema()
    missing = RUNNER_CHAT_RESPONSE_FIELDS - set(schema["properties"])
    if missing:
        _fail(f"ChatResponse dropped runner-read fields {sorted(missing)}")
    tool_call = _resolve(schema["properties"]["tool_calls"], schema)
    missing = RUNNER_TOOL_CALL_FIELDS - set(tool_call["properties"])
    if missing:
        _fail(f"ChatResponse.tool_calls items dropped runner-read fields {sorted(missing)}")


def test_oauth_callback_contract():
    schema = OAuthCallbackRequest.model_json_schema()
    missing = RUNNER_OAUTH_FIELDS - set(schema["properties"])
    if missing:
        _fail(f"OAuthCallbackRequest dropped fields {sorted(missing)}")
    if set(schema.get("required", [])) - {"toolset_name", "code", "redirect_uri"}:
        _fail("OAuthCallbackRequest requires fields the UI/runner may not send")
    if "success" not in OAuthCallbackResponse.model_json_schema()["properties"]:
        _fail("OAuthCallbackResponse dropped 'success', which the runner reads")


def test_stream_events_the_runner_relays_still_exist():
    missing = RUNNER_STREAM_EVENTS - {e.value for e in StreamEvents}
    if missing:
        _fail(f"/api/chat stream no longer emits events {sorted(missing)}")


# ---- 2. snapshot ----


def _diff_paths(expected: Any, actual: Any, path: str = "$") -> list:
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                out.append(f"removed {path}.{key}")
            elif key not in expected:
                out.append(f"added   {path}.{key}")
            else:
                out.extend(_diff_paths(expected[key], actual[key], f"{path}.{key}"))
        return out
    if expected != actual:
        return [f"changed {path}: {json.dumps(expected)[:120]} -> {json.dumps(actual)[:120]}"]
    return []


def test_contract_matches_snapshot():
    current = _current_contract()
    if os.environ.get(UPDATE_ENV):
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    if not SNAPSHOT.exists():
        pytest.fail(f"missing snapshot {SNAPSHOT}; generate it with {UPDATE_ENV}=1", pytrace=False)
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    # Round-trip so tuples/ordering compare the way the snapshot stores them.
    current = json.loads(json.dumps(current, sort_keys=True))
    if current != expected:
        diff = "\n  ".join(_diff_paths(expected, current)[:40])
        pytest.fail(
            "Runner <-> Holmes API schemas differ from the committed snapshot "
            f"({SNAPSHOT.relative_to(REPO_ROOT)}):\n  {diff}\n\n"
            "Removing or renaming a field, or making one required, breaks runners "
            "that are not upgraded with Holmes. If the change is additive and "
            f"intended, regenerate the snapshot:\n  {UPDATE_ENV}=1 poetry run pytest "
            "tests/test_runner_api_contract.py --no-cov\nand commit it.",
            pytrace=False,
        )
