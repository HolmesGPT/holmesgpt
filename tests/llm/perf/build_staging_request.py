#!/usr/bin/env python3
"""Build a self-contained wizard-request fixture for replay against staging Holmes,
and mint a relay session token.

Because agent-browser's Chrome cannot egress through the sandbox proxy, we can't
capture the UI's request from the browser. Instead we reconstruct it exactly from
the same sources the frontend uses, authenticating via curl/HTTP (which the proxy
does allow):

  1. Supabase password login (E2E creds) -> access JWT + user_id/email.
  2. Insert a random UUID into `FrontendSessionTokens` (what the UI does on login);
     the relay validates the session_token against this table.
  3. POST {relay}/api/llm/models/v2 -> the account's default Holmes model (what
     `getCurrentModel` in holmes-llm.pinia.store.ts resolves to).
  4. Assemble the `additional_system_prompt` = frontend base prompt
     (robusta-frontend public/api/additional-system-prompt.json) + the MCP
     setup-guidance/rules + the 4 noop frontend tools (from the eval fixture
     tests/llm/fixtures/shared/data_source_mcp_setup.yaml), and the inlined-answers
     `ask` (from tests/.../283_mcp_data_source_setup/test_case.yaml).
  5. Write a self-contained request body to fixtures/mcp_wizard_request.json with
     session_token = "" (nothing secret committed), and print the minted token +
     model to stdout for the replay step.

Env (required): E2E_USER_EMAIL, E2E_USER_PASSWORD.
Env (optional): SUPABASE_URL, SUPABASE_ANON_KEY, RELAY_HOST, CLUSTER_NAME,
                FRONTEND_BASE_PROMPT (path to additional-system-prompt.json).
"""

import json
import os
import sys
import uuid

import requests
import yaml

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://hfzzthymmksjyasfrdef.supabase.co")
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imhmenp0aHltbWtzanlhc2ZyZGVmIiwicm9sZSI6ImFub24iLCJpYXQiOjE2NDcwODIzMjcsImV4cCI6MTk2MjY1ODMyN30.ZVd-umVMGXmW-3DkxacD21JTl6vfWr85O1xsy989kh4",
)
RELAY_HOST = os.environ.get("RELAY_HOST", "https://stg.api.robusta.dev")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "fdcda7eb-23eb-4798-b856-b146b60bced8")
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "e2e-tests")
# When a caller-supplied session token is used (its account/user may differ from
# the E2E login), take identity from env instead of logging in.
ENV_USER_ID = os.environ.get("USER_ID")
ENV_USER_EMAIL = os.environ.get("USER_EMAIL")
FRONTEND_BASE_PROMPT = os.environ.get(
    "FRONTEND_BASE_PROMPT",
    "/home/user/robusta-frontend/public/api/additional-system-prompt.json",
)

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED_FIXTURE = os.path.join(HERE, "..", "fixtures", "shared", "data_source_mcp_setup.yaml")
TEST_CASE = os.path.join(HERE, "..", "fixtures", "test_ask_holmes", "283_mcp_data_source_setup", "test_case.yaml")
OUT_FIXTURE = os.path.join(HERE, "fixtures", "mcp_wizard_request.json")


def login():
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": os.environ["E2E_USER_EMAIL"], "password": os.environ["E2E_USER_PASSWORD"]},
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()
    return d["access_token"], d["user"]["id"], d["user"]["email"]


def create_session_token(access, user_id):
    token = str(uuid.uuid4())
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/FrontendSessionTokens",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json={"user_id": user_id, "account_id": ACCOUNT_ID, "token": token},
        timeout=20,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"FrontendSessionTokens insert failed: HTTP {r.status_code}: {r.text[:300]}")
    return token


def fetch_default_model(session_token):
    r = requests.post(
        f"{RELAY_HOST}/api/llm/models/v2",
        headers={"Content-Type": "application/json"},
        json={"account_id": ACCOUNT_ID, "session_token": session_token},
        timeout=30,
    )
    r.raise_for_status()
    models = r.json()
    default = next((m for m, meta in models.items() if meta.get("is_default")), None)
    platform_default = next((m for m, meta in models.items() if meta.get("is_platform_default")), None)
    return default or platform_default, list(models.keys())


def build_additional_system_prompt():
    with open(FRONTEND_BASE_PROMPT, "r", encoding="utf-8") as f:
        base = json.load(f)["additional_system_prompt"]
    with open(SHARED_FIXTURE, "r", encoding="utf-8") as f:
        shared = yaml.safe_load(f)
    guidance = shared["additional_system_prompt"]
    tools = shared["frontend_tools"]
    # Mirrors DataSourceTroubleshootStep.buildTroubleshootAdditionalSystemPrompt:
    # base, then the multiple-choice/secrets/style/status rules + setup guidance
    # (the shared fixture already carries that block in order).
    full = f"{base}\n\n{guidance}"
    return full, tools


def main():
    if not os.environ.get("E2E_USER_EMAIL") or not os.environ.get("E2E_USER_PASSWORD"):
        print("ERROR: set E2E_USER_EMAIL and E2E_USER_PASSWORD", file=sys.stderr)
        return 2

    with open(TEST_CASE, "r", encoding="utf-8") as f:
        ask = yaml.safe_load(f)["user_prompt"]

    session_token = os.environ.get("ROBUSTA_STAGING_SESSION_TOKEN")
    if session_token:
        # Caller-supplied token: it belongs to whatever account/user minted it, so
        # do NOT log in as the E2E user (that identity would mismatch the token).
        # Take identity from env; user_id/email are optional attribution fields.
        print("[token] using ROBUSTA_STAGING_SESSION_TOKEN from env", file=sys.stderr)
        user_id = ENV_USER_ID or ""
        user_email = ENV_USER_EMAIL or ""
    else:
        # Self-mint path (only where RLS allows): log in as the E2E user + insert.
        access, user_id, user_email = login()
        print(f"[auth] user_id={user_id} email={user_email}", file=sys.stderr)
        session_token = create_session_token(access, user_id)
        print(f"[token] created FrontendSessionTokens row: {session_token}", file=sys.stderr)

    model, all_models = fetch_default_model(session_token)
    print(f"[model] default={model!r} (of {len(all_models)} available)", file=sys.stderr)

    additional_system_prompt, frontend_tools = build_additional_system_prompt()

    body = {
        "body": {
            "cluster_name": CLUSTER_NAME,
            "action_name": "holmes_chat",
            "action_params": {
                "ask": ask,
                "additional_system_prompt": additional_system_prompt,
                "frontend_tools": frontend_tools,
                "model": model,
                "stream": True,
                "enable_tool_approval": False,
                "request_source": "data_source_setup",
                "behavior_controls": {
                    "toolset_instructions": False,
                    "todowrite_instructions": False,
                    "todowrite_reminder": False,
                    "time_runbooks": False,
                },
                "user_id": user_id,
                "user_email": user_email,
            },
            "account_id": ACCOUNT_ID,
            "origin": "Robusta UI",
            "timestamp": 0,
        },
        "session_token": "",  # injected at run time from env; never committed
        "no_sinks": True,
    }

    os.makedirs(os.path.dirname(OUT_FIXTURE), exist_ok=True)
    with open(OUT_FIXTURE, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    print(f"[fixture] wrote {OUT_FIXTURE} (session_token stripped)", file=sys.stderr)
    print(f"[fixture] additional_system_prompt: {len(additional_system_prompt)} chars, "
          f"{len(frontend_tools)} frontend tools, model={model}", file=sys.stderr)

    # machine-readable line for the runner (last line of stdout)
    print(json.dumps({"session_token": session_token, "model": model, "cluster": CLUSTER_NAME,
                      "account_id": ACCOUNT_ID, "user_id": user_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
