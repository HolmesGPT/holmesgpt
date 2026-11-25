"""Opt-in HTTP APIs (ROB-1927).

/api/chat, /api/checks/*, /api/oauth/callback and /api/info answer 404 unless
their ENABLE_*_API flag is set. Health probes and /api/model stay open.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from holmes.common import env_vars
from holmes.core.models import OAuthCallbackResponse
from holmes.core.tool_calling_llm import LLMResult
from holmes.utils.auth import OPT_IN_API_FLAGS, disabled_api_flag, require_api_enabled
from server import app, log_opt_in_apis

REPO_ROOT = Path(__file__).resolve().parents[1]

OAUTH_BODY = {"toolset_name": "t", "code": "c", "redirect_uri": "https://x/cb"}
CHECK_BODY = {"query": "is it up?"}

# (flag, method, path, valid body)
GATED = [
    ("ENABLE_CHAT_API", "post", "/api/chat", {"ask": "hi"}),
    ("ENABLE_CHAT_API", "post", "/api/chat", {"ask": "hi", "stream": True}),
    ("ENABLE_CHECKS_API", "post", "/api/checks/execute", CHECK_BODY),
    ("ENABLE_OAUTH_API", "post", "/api/oauth/callback", OAUTH_BODY),
    ("ENABLE_INFO_API", "get", "/api/info", None),
    ("ENABLE_INFO_API", "get", "/api/info?detail=full", None),
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def all_disabled(monkeypatch):
    for flag in OPT_IN_API_FLAGS:
        monkeypatch.setattr(env_vars, flag, False)


@pytest.fixture
def handlers():
    """Everything a gated handler would reach, mocked so a call is visible."""
    ai = MagicMock()
    ai.llm.model = "m"
    ai.call.return_value = LLMResult(
        result='{"passed": true, "rationale": "ok"}', tool_calls=[], messages=[]
    )
    with (
        patch("holmes.config.Config.create_toolcalling_llm", return_value=ai) as llm,
        patch(
            "holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account",
            return_value=[],
        ),
        patch(
            "server.handle_oauth_callback",
            return_value=OAuthCallbackResponse(success=True),
        ) as oauth,
        patch("holmes.config.Config.create_server_tool_executor") as executor,
    ):
        executor.return_value.toolsets = []
        yield {"llm": llm, "oauth": oauth, "executor": executor}


def _call(client, method, path, body):
    return client.request(method, path, json=body)


@pytest.mark.parametrize("flag, method, path, body", GATED)
def test_disabled_api_answers_404_and_runs_nothing(
    client, all_disabled, handlers, flag, method, path, body
):
    response = _call(client, method, path, body)

    assert response.status_code == 404
    assert flag in response.json()["detail"]
    for mock in handlers.values():
        mock.assert_not_called()


@pytest.mark.parametrize(
    "method, path",
    [("post", "/api/chat"), ("post", "/api/checks/execute"), ("post", "/api/oauth/callback")],
)
def test_disabled_api_does_not_read_the_body(client, all_disabled, method, path):
    """A 422 would confirm the API is live and leak its schema."""
    assert _call(client, method, path, {"unexpected": 1}).status_code == 404
    for headers in ({}, {"Content-Type": "application/json"}):
        response = client.request(method, path, content=b"not json", headers=headers)
        assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/api/chat/", "/api/checks", "/api/checks/", "/api/checks/execute/", "/api/checks/other"],
)
def test_disabled_api_path_variants_stay_closed(client, all_disabled, handlers, path):
    response = client.post(path, json={"ask": "hi", "query": "q"})

    assert response.status_code in (404, 405)
    handlers["llm"].assert_not_called()


def test_health_and_model_stay_open_when_everything_is_disabled(client, all_disabled):
    assert client.get("/healthz").status_code == 200
    with patch("holmes.config.Config.get_models_list", return_value=["m"]):
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/model").json() == {"model_name": '["m"]'}


@pytest.mark.parametrize("flag, method, path, body", GATED)
def test_enabled_api_reaches_its_handler(
    client, all_disabled, handlers, monkeypatch, flag, method, path, body
):
    monkeypatch.setattr(env_vars, flag, True)

    assert _call(client, method, path, body).status_code == 200


@pytest.mark.parametrize("enabled", OPT_IN_API_FLAGS)
def test_each_flag_opens_only_its_own_api(
    client, all_disabled, handlers, monkeypatch, enabled
):
    monkeypatch.setattr(env_vars, enabled, True)

    for flag, method, path, body in GATED:
        expected = 200 if flag == enabled else 404
        assert _call(client, method, path, body).status_code == expected, path


def test_every_flag_gates_an_endpoint():
    assert {flag for flag, *_ in GATED} == set(OPT_IN_API_FLAGS)


# Served without a flag on purpose; anything else must sit under an opt-in prefix.
ALWAYS_OPEN = {
    "/healthz",
    "/readyz",
    "/api/model",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def _scope(path, root_path=""):
    return {"path": path, "root_path": root_path}


def test_every_route_is_either_opt_in_or_deliberately_open(all_disabled):
    paths = {r.path for r in app.routes if not r.path.startswith("/__test")}

    ungated = {p for p in paths if disabled_api_flag(_scope(p)) is None}
    assert ungated == ALWAYS_OPEN


@pytest.mark.parametrize(
    "path", ["/api/chatx", "/api/chat-other", "/api/information", "/api", "/"]
)
def test_a_path_that_only_shares_a_prefix_is_not_gated(all_disabled, path):
    assert disabled_api_flag(_scope(path)) is None


@pytest.mark.parametrize("flag, method, path, body", GATED)
def test_disabled_api_stays_closed_behind_a_root_path(
    all_disabled, handlers, flag, method, path, body
):
    """A proxy prefix reaches the app as root_path, which the router strips."""
    prefixed = TestClient(app, root_path="/holmes")

    response = _call(prefixed, method, "/holmes" + path, body)

    assert response.status_code == 404
    assert flag in response.json()["detail"]
    for mock in handlers.values():
        mock.assert_not_called()


@pytest.mark.parametrize("flag, method, path, body", GATED)
def test_route_itself_refuses_when_the_path_gate_is_passed(
    client, all_disabled, handlers, flag, method, path, body
):
    with patch("server.disabled_api_flag", return_value=None):
        response = _call(client, method, path, body)

    assert response.status_code == 404
    assert flag in response.json()["detail"]
    for mock in handlers.values():
        mock.assert_not_called()


def test_unknown_flag_is_rejected():
    with pytest.raises(ValueError):
        require_api_enabled("ENABLE_TYPO_API")


@pytest.mark.parametrize(
    "chat_api, from_platform, warns",
    [(False, False, True), (False, True, False), (True, False, False), (True, True, False)],
)
def test_startup_warns_when_no_chat_entrypoint_is_left(
    monkeypatch, caplog, chat_api, from_platform, warns
):
    monkeypatch.setattr(env_vars, "ENABLE_CHAT_API", chat_api)

    with caplog.at_level("INFO"):
        log_opt_in_apis(chats_arrive_from_platform=from_platform)

    assert f"ENABLE_CHAT_API={str(chat_api).lower()}" in caplog.text
    assert ("Holmes cannot receive chats" in caplog.text) is warns


def _flags_in_fresh_process(extra_env):
    env = {k: v for k, v in os.environ.items() if k not in OPT_IN_API_FLAGS}
    env.update(extra_env)
    code = (
        "from holmes.common import env_vars;"
        "from holmes.utils.auth import OPT_IN_API_FLAGS;"
        "print({f: getattr(env_vars, f) for f in OPT_IN_API_FLAGS})"
    )
    return subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=REPO_ROOT, capture_output=True, text=True
    )


def test_all_opt_in_apis_are_off_by_default():
    result = _flags_in_fresh_process({})

    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(result.stdout) == {flag: False for flag in OPT_IN_API_FLAGS}


@pytest.mark.parametrize(
    "value, expected", [("true", True), ("True", True), ("TRUE", True), ("false", False)]
)
def test_flag_is_read_from_the_environment(value, expected):
    result = _flags_in_fresh_process({"ENABLE_CHAT_API": value})

    flags = yaml.safe_load(result.stdout)
    assert flags["ENABLE_CHAT_API"] is expected
    assert flags["ENABLE_CHECKS_API"] is False


@pytest.mark.parametrize("value", ["yes", "", "enabled", "1", "0", '"false"', '"true"', "null", "[]"])
def test_invalid_flag_value_fails_startup_instead_of_guessing(value):
    assert _flags_in_fresh_process({"ENABLE_CHAT_API": value}).returncode != 0


def _helm_holmes_env(*args):
    output = subprocess.check_output(
        ["helm", "template", "r", str(REPO_ROOT / "helm" / "holmes"), "-s", "templates/holmes.yaml", *args],
        text=True,
    )
    deployment = next(d for d in yaml.safe_load_all(output) if d and d["kind"] == "Deployment")
    return {e["name"]: e.get("value") for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not available")
def test_helm_enables_only_the_checks_api_and_only_with_the_operator():
    assert not set(OPT_IN_API_FLAGS) & set(_helm_holmes_env())

    with_operator = _helm_holmes_env("--set", "operator.enabled=true")
    assert with_operator["ENABLE_CHECKS_API"] == "true"
    assert set(OPT_IN_API_FLAGS) & set(with_operator) == {"ENABLE_CHECKS_API"}
