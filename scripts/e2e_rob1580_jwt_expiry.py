"""ROB-1580 e2e: Holmes keeps working after its Supabase session expires.

Runs Holmes' own SupabaseDal and robusta_platform_mcp toolset against the
robusta-on-premise kind platform (`e2e/spinup.sh --values <JWT_EXPIRY: 90>`).
Twice, revokes the refresh tokens of Holmes' account user and of the relay
user (the customer's state after a refresh got a 400) and waits past JWT
expiry; the first call after each expiry is a different path:

  round 1: an RPC through Holmes' DAL -> succeeds
  round 2: reloading the robusta_platform_mcp toolset (a HOLMES session-token
           insert through Holmes' DAL, then tools/list on platform-mcp)
           -> enabled, with tools

Usage (KUBECONFIG pointing at the kind cluster):
  ROBUSTA_UI_TOKEN=<token with store_url=http://localhost:30312> \\
  ROBUSTA_API_ENDPOINT=http://localhost:30313 \\
  ROBUSTA_MCP_ENDPOINT=http://localhost:30315/api/platform-mcp \\
      poetry run python scripts/e2e_rob1580_jwt_expiry.py
"""

import json
import logging
import os
import subprocess
import sys
import time

from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.robusta_platform_mcp.robusta_platform_mcp import (
    make_robusta_platform_mcp_toolset,
)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

NS = os.environ.get("E2E_NAMESPACE", "robusta")
PGPASSWORD = os.environ.get("E2E_PGPASSWORD", "test-postgres-password")
RELAY_EMAIL = os.environ.get("E2E_RELAY_EMAIL", "apiuser-robustarelay@robusta.dev")
POSTGREST_JWT_LEEWAY_SECONDS = 30


def sql(query: str) -> str:
    return subprocess.run(
        [
            "kubectl",
            "-n",
            NS,
            "exec",
            "-i",
            "db-0",
            "--",
            "env",
            f"PGPASSWORD={PGPASSWORD}",
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            query,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def jwt_expiry() -> int:
    out = subprocess.run(
        [
            "kubectl",
            "-n",
            NS,
            "get",
            "deploy/auth",
            "-o",
            "jsonpath={.spec.template.spec.containers[0].env}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(
        next(e["value"] for e in json.loads(out) if e["name"] == "GOTRUE_JWT_EXP")
    )


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def rpc(dal: SupabaseDal) -> str:
    """Call an RPC straight through the patched execute; any error fails the
    check, unlike is_realtime_enabled() which maps a missing function to False."""
    try:
        return f"ok: {dal.client.rpc('is_realtime_enabled', {}).execute().data}"
    except Exception as e:
        return f"error: {e}"


def load(toolset) -> dict:
    toolset.check_prerequisites(silent=True)
    return {
        "status": toolset.status.value,
        "error": toolset.error,
        "tools": len(toolset.tools),
    }


def main() -> int:
    dal = SupabaseDal(cluster=os.environ.get("CLUSTER_NAME", "e2e-test"))
    assert dal.enabled, "SupabaseDal not enabled; check ROBUSTA_UI_TOKEN"
    toolset = make_robusta_platform_mcp_toolset(dal)
    evidence: dict = {"jwt_expiry": jwt_expiry(), "before_expiry": load(toolset)}
    evidence["before_expiry"]["rpc"] = rpc(dal)

    def expire():
        revoked = sql(
            "delete from auth.sessions where user_id in (select id from auth.users "
            f"where email in ({literal(dal.email)}, {literal(RELAY_EMAIL)})) returning id"
        )
        wait = evidence["jwt_expiry"] + POSTGREST_JWT_LEEWAY_SECONDS + 15
        logging.info(
            "revoked %s sessions; waiting %ss", len(revoked.splitlines()), wait
        )
        time.sleep(wait)

    expire()
    evidence["rpc_first_after_expiry"] = rpc(dal)

    expire()
    dal.token_cache.clear()
    evidence["toolset_first_after_expiry"] = load(
        make_robusta_platform_mcp_toolset(dal)
    )

    after = evidence["toolset_first_after_expiry"]
    checks = {
        "rpc works before expiry": evidence["before_expiry"]["rpc"].startswith("ok"),
        "rpc answers after expiry": evidence["rpc_first_after_expiry"].startswith("ok"),
        "toolset loads after expiry": after["status"] == ToolsetStatusEnum.ENABLED.value
        and after["tools"] > 0,
    }
    evidence["checks"] = checks
    print(json.dumps(evidence, indent=2))
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
