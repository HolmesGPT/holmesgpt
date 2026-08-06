"""End-to-end test: a YAMLTool invocation must run kubectl with the *caller's*
request-scoped credential, and two concurrent callers must never see each
other's identity.

A fake ``kubectl`` on PATH echoes back the token it was handed via KUBECONFIG,
so we can assert on the identity the command actually ran as.
"""

import concurrent.futures
import os
import stat
import textwrap

import pytest

from holmes.core.tools import ToolInvokeContext, YAMLTool
from tests.conftest import MockLLM

AUTH_MODE = "HOLMES_K8S_AUTH_MODE"


@pytest.fixture
def fake_kubectl(tmp_path, monkeypatch):
    """A stand-in kubectl that prints the token from its kubeconfig."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            # Report which identity (and kubeconfig) this invocation received.
            # Quotes are stripped so we compare the resolved YAML scalar value.
            echo "KUBECONFIG=${KUBECONFIG:-<unset>}"
            if [ -n "$KUBECONFIG" ] && [ -f "$KUBECONFIG" ]; then
                grep -E "^\\s+token:" "$KUBECONFIG" | tr -d " '"
            else
                echo "token:<none>"
            fi
            """
        )
    )
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return kubectl


@pytest.fixture
def ca_file(tmp_path, monkeypatch):
    ca = tmp_path / "ca.crt"
    ca.write_text("FAKE-CA")
    monkeypatch.setenv("HOLMES_K8S_CA_CERT", str(ca))
    monkeypatch.setenv("HOLMES_K8S_API_SERVER", "https://api.test:6443")
    return ca


def _tool() -> YAMLTool:
    return YAMLTool(
        name="k8s_probe",
        description="probe identity",
        command="kubectl get pods",
    )


def _ctx(token: str) -> ToolInvokeContext:
    return ToolInvokeContext(
        llm=MockLLM(),
        max_token_count=10000,
        tool_call_id="call-1",
        tool_name="k8s_probe",
        request_context={"headers": {"X-Auth-Request-Id-Token": token}},
    )


def test_tool_runs_with_request_scoped_identity(fake_kubectl, ca_file, monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    result = _tool().invoke({}, context=_ctx("Bearer USER-ALICE"))
    # kubectl saw a KUBECONFIG, and it carried Alice's (de-prefixed) token.
    assert "token:USER-ALICE" in result.data
    assert "KUBECONFIG=<unset>" not in result.data
    # The rendered invocation must NOT contain the credential.
    assert "USER-ALICE" not in (result.invocation or "")


def test_disabled_mode_does_not_inject_kubeconfig(fake_kubectl, ca_file, monkeypatch):
    monkeypatch.delenv(AUTH_MODE, raising=False)  # default: service_account
    monkeypatch.delenv("KUBECONFIG", raising=False)
    result = _tool().invoke({}, context=_ctx("Bearer USER-ALICE"))
    assert "KUBECONFIG=<unset>" in result.data


def test_concurrent_users_never_cross_identities(fake_kubectl, ca_file, monkeypatch):
    """The core anti-regression test for cross-user credential bleed."""
    monkeypatch.setenv(AUTH_MODE, "request_token")
    tool = _tool()
    users = [f"USER-{i:03d}" for i in range(60)]

    def run(user: str):
        res = tool.invoke({}, context=_ctx(f"Bearer {user}"))
        return user, res.data

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(run, users))

    for user, data in results:
        assert f"token:{user}" in data, f"{user} did not get its own token"
        # No other user's identity may appear in this invocation's output.
        others = [u for u in users if u != user and f"token:{u}" in data]
        assert not others, f"{user} saw foreign identities: {others}"


def test_no_global_state_mutation_and_no_leftover_files(
    fake_kubectl, ca_file, monkeypatch
):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    before_env = dict(os.environ)
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    before_files = {f for f in os.listdir(tmpdir) if f.startswith("holmes-kubeconfig-")}

    _tool().invoke({}, context=_ctx("Bearer USER-BOB"))

    # os.environ must be untouched (no global KUBECONFIG).
    assert dict(os.environ) == before_env
    assert "KUBECONFIG" not in os.environ or before_env.get("KUBECONFIG")
    # Temp kubeconfig must be gone.
    after_files = {f for f in os.listdir(tmpdir) if f.startswith("holmes-kubeconfig-")}
    assert after_files == before_files
