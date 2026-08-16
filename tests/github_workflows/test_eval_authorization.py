"""Regression tests for the /eval slash-command authorization gate (ROB-1099).

The eval workflow runs in the base repository on `issue_comment`, so it holds a
write-scoped GITHUB_TOKEN and every repository secret while it executes a pull
request's code. It used to decide who was allowed to do that from
`comment.author_association`, which reports COLLABORATOR for outside
collaborators holding only `read` or `triage` — so a read-level collaborator
could authorize a fork's code to run against the full credential set, and could
supply that fork themselves.

These tests pin the two rules that replaced it:

  1. Authorization comes from the actor's real repository permission.
  2. Releasing a fork's code takes two people.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "authorization_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to exercise the GitHub Actions authorization helpers",
)


def call_js(fn: str, args: dict, permission_api: dict | None = None) -> dict:
    """Run one function from .github/scripts/eval-authorization.js under node."""
    request = {"fn": fn, "args": args}
    if permission_api is not None:
        request["permissionApi"] = permission_api

    completed = subprocess.run(
        ["node", str(HARNESS), json.dumps(request)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        completed.returncode == 0
    ), f"harness failed ({completed.returncode}): {completed.stderr}"
    return json.loads(completed.stdout)


def authorize_command(actor: str = "someone", **permission_api) -> dict:
    return call_js(
        "authorizeCommand",
        {
            "owner": "HolmesGPT",
            "repo": "holmesgpt",
            "actor": actor,
            "command": "/eval",
        },
        permission_api=permission_api,
    )


def authorize_fork_run(**overrides) -> dict:
    args = {
        "actor": "maintainer",
        "prAuthor": "contributor",
        "headRepoOwner": "contributor",
        "headSha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "trusted": "true",
        "command": "/eval",
    }
    args.update(overrides)
    return call_js("authorizeForkRun", args)["result"]


# --- Rule 1: real permission, never author_association ----------------------


@pytest.mark.parametrize("permission", ["read", "none"])
def test_read_level_collaborator_is_denied(permission):
    """A read/triage outside collaborator reports author_association=COLLABORATOR
    but must not be able to start the secret-bearing job."""
    response = authorize_command(actor="outside-collab", permission=permission)
    decision = response["result"]

    assert decision["authorized"] is False
    assert decision["permission"] == permission
    assert "outside-collab" in decision["reason"]
    assert "requires `write` or `admin`" in decision["reason"]


def test_permission_is_queried_for_the_commenter():
    """The permission lookup must be for the commenter, not the PR author or actor."""
    response = authorize_command(actor="outside-collab", permission="read")

    assert response["calls"] == [
        {"owner": "HolmesGPT", "repo": "holmesgpt", "username": "outside-collab"}
    ]


@pytest.mark.parametrize("permission", ["admin", "write"])
def test_write_and_admin_are_allowed(permission):
    decision = authorize_command(actor="maintainer", permission=permission)["result"]

    assert decision["authorized"] is True
    assert decision["permission"] == permission


def test_maintain_role_reports_as_write_and_is_allowed():
    """The legacy `permission` field collapses `maintain` to `write` (and, the
    reason this gate exists, `triage` to `read`)."""
    decision = authorize_command(
        actor="maintainer", permission="write", role_name="maintain"
    )["result"]

    assert decision["authorized"] is True


def test_non_collaborator_404_is_denied():
    """GitHub 404s for a user with no relationship to the repo."""
    decision = authorize_command(actor="stranger", status=404)["result"]

    assert decision["authorized"] is False
    assert decision["permission"] == "none"


def test_api_failure_fails_closed():
    """An unverifiable permission is not an authorized one."""
    decision = authorize_command(
        actor="maintainer", status=500, message="server error"
    )["result"]

    assert decision["authorized"] is False
    assert decision["permission"] == "unknown"
    assert "could not verify" in decision["reason"].lower()


def test_missing_actor_is_denied():
    decision = call_js(
        "authorizeCommand",
        {"owner": "HolmesGPT", "repo": "holmesgpt", "actor": "", "command": "/eval"},
        permission_api={"permission": "admin"},
    )["result"]

    assert decision["authorized"] is False


# --- Rule 2: releasing a fork takes two people ------------------------------


def test_pr_author_cannot_release_their_own_fork():
    """`trusted: true` from the PR author alone must not release secrets."""
    decision = authorize_fork_run(actor="contributor", prAuthor="contributor")

    assert decision["authorized"] is False
    assert decision["code"] == "self_approval"
    assert "cannot also authorize" in decision["reason"]


def test_pr_author_check_is_case_insensitive():
    """GitHub logins are case-insensitive; the gate must not be bypassable by case."""
    decision = authorize_fork_run(actor="Contributor", prAuthor="contributor")

    assert decision["authorized"] is False
    assert decision["code"] == "self_approval"


def test_fork_owner_cannot_release_a_pr_opened_by_someone_else():
    """The fork owner can push new commits to the branch being run, so they are
    just as much the submitter as whoever clicked 'open pull request'."""
    decision = authorize_fork_run(
        actor="fork-owner", prAuthor="colleague", headRepoOwner="fork-owner"
    )

    assert decision["authorized"] is False
    assert decision["code"] == "self_approval"


def test_second_maintainer_can_release_a_fork():
    decision = authorize_fork_run(actor="maintainer", prAuthor="contributor")

    assert decision["authorized"] is True
    assert decision["code"] == "ok"


def test_missing_trust_directive_is_denied():
    decision = authorize_fork_run(trusted=None)

    assert decision["authorized"] is False
    assert decision["code"] == "missing_trust"
    assert decision["comment"], "the PR should get an explanation of how to proceed"


def test_unknown_pr_author_fails_closed():
    """Without a PR author there is no way to enforce the two-party rule."""
    decision = authorize_fork_run(prAuthor=None)

    assert decision["authorized"] is False
    assert decision["code"] == "unknown_author"


# --- `trusted: <sha>` pins the review to the reviewed commit ----------------


def test_trusted_sha_matching_head_is_allowed():
    decision = authorize_fork_run(trusted="a1b2c3d")

    assert decision["authorized"] is True
    assert decision["pinnedSha"] == "a1b2c3d"


def test_trusted_sha_is_case_insensitive():
    decision = authorize_fork_run(trusted="A1B2C3D4E5F6")

    assert decision["authorized"] is True


def test_trusted_sha_from_a_superseded_commit_is_denied():
    """Closes the window between reviewing a diff and the job resolving HEAD."""
    decision = authorize_fork_run(trusted="9999999")

    assert decision["authorized"] is False
    assert decision["code"] == "stale_sha"


def test_garbage_trust_value_is_denied():
    decision = authorize_fork_run(trusted="yes-please")

    assert decision["authorized"] is False
    assert decision["code"] == "invalid_trust"


# --- directive parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("/eval\ntrusted: true\ntags: regression", "true"),
        # The raw token is returned as written; authorizeForkRun normalises it.
        ("/eval\nTRUSTED:TRUE", "TRUE"),
        ("/eval\n  trusted :  true  ", "true"),
        ("/eval\ntags: regression\r\ntrusted: true\r\n", "true"),
    ],
)
def test_trusted_directive_is_recognised(body, expected):
    assert call_js("parseTrustedDirective", {"body": body})["result"] == {
        "value": expected
    }


def test_uppercase_trust_directive_is_honoured_end_to_end():
    """Case normalisation is authorizeForkRun's job, not the parser's."""
    assert authorize_fork_run(trusted="TRUE")["authorized"] is True


@pytest.mark.parametrize(
    "body",
    [
        "/eval\ntags: regression",
        "",
        # Must be its own line, not prose mentioning the directive.
        "/eval\nplease note trusted: true is needed here",
        # A newline between the key and value would let the directive straddle
        # lines; only horizontal whitespace is allowed.
        "/eval\ntrusted:\ntrue",
    ],
)
def test_non_directives_are_not_recognised(body):
    assert call_js("parseTrustedDirective", {"body": body})["result"] is None


def test_directive_captures_a_sha_value():
    assert call_js("parseTrustedDirective", {"body": "/eval\ntrusted: a1b2c3d\n"})[
        "result"
    ] == {"value": "a1b2c3d"}
