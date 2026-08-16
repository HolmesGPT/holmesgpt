"""Structural regression tests for .github/workflows/eval-regression.yaml (ROB-1099).

tests/github_workflows/test_eval_authorization.py covers the authorization logic.
These tests cover the wiring around it — the parts that live in YAML and can be
undone by an edit that never touches the JS: which gate the workflow calls, and
where it loads executable code from when the pull request is a fork.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).parent.parent.parent
    / ".github"
    / "workflows"
    / "eval-regression.yaml"
)

# Jobs that act on an `issue_comment` slash command, and therefore run in the base
# repository with secrets and a write-scoped token.
SLASH_COMMAND_JOBS = ["list_evals", "llm_evals"]


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text()


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict:
    return yaml.safe_load(workflow_text)


def steps_of(workflow: dict, job: str) -> list:
    return workflow["jobs"][job]["steps"]


def script_of(step: dict) -> str:
    return (step.get("with") or {}).get("script", "")


def test_author_association_is_not_used_anywhere(workflow_text: str):
    """The root cause. `author_association` is relationship metadata: GitHub
    returns COLLABORATOR for outside collaborators with only read or triage
    access, so it must never gate anything that touches secrets. Mentions in
    comments are fine — they are why it must not be used."""
    offenders = [
        line.strip()
        for line in workflow_text.splitlines()
        if "author_association" in line and not line.strip().startswith(("#", "//"))
    ]

    assert not offenders, f"eval-regression.yaml must not authorize anything from author_association: {offenders}"


@pytest.mark.parametrize("job", SLASH_COMMAND_JOBS)
def test_slash_command_jobs_query_real_permission(workflow: dict, job: str):
    """Every slash-command entry point must go through authorizeCommand, which
    asks the API for the actor's actual permission level."""
    scripts = "\n".join(script_of(step) for step in steps_of(workflow, job))

    assert (
        "eval-authorization.js" in scripts
    ), f"job {job} does not load the authorization helpers"
    assert (
        "authorizeCommand" in scripts
    ), f"job {job} does not check the actor's repository permission"


def test_fork_runs_go_through_the_two_party_gate(workflow: dict):
    """A fork's code may only be released by someone other than its submitter."""
    scripts = "\n".join(script_of(step) for step in steps_of(workflow, "llm_evals"))

    assert "authorizeForkRun" in scripts
    # Both fork-capable entry points: /eval comments and workflow_dispatch. If you
    # added a third way to run fork code, gate it too and bump this count.
    assert (
        scripts.count("authorizeForkRun(") == 2
    ), "every path that can run fork code must call authorizeForkRun"


def test_authorization_helpers_load_from_the_trusted_checkout(workflow: dict):
    """Loading them from ./code/ would let the pull request rewrite its own gate."""
    for job in SLASH_COMMAND_JOBS:
        for step in steps_of(workflow, job):
            script = script_of(step)
            if "eval-authorization.js" not in script:
                continue
            assert (
                "./.trusted/.github/scripts/eval-authorization.js" in script
                or "./.github/scripts/eval-authorization.js" in script
            ), f"job {job} loads the authorization helpers from an untrusted path"
            assert "./code/" not in script


def test_no_step_runs_an_action_from_the_pr_checkout(workflow: dict):
    """`uses: ./code/...` executes action YAML the fork controls, before pytest and
    with every secret already in the job. Composite actions come from ./.trusted/."""
    offenders = [
        (job, step.get("name"), step["uses"])
        for job, config in workflow["jobs"].items()
        for step in config.get("steps", [])
        if step.get("uses", "").startswith("./code/")
    ]

    assert not offenders, f"steps run actions from the PR checkout: {offenders}"


def test_trusted_checkout_supplies_scripts_and_actions(workflow: dict):
    """Both trusted trees must be present, or the `uses:`/`require()` above break."""
    trusted_checkouts = [
        step
        for step in steps_of(workflow, "llm_evals")
        if (step.get("with") or {}).get("path") == ".trusted"
    ]
    assert len(trusted_checkouts) == 1, "expected exactly one ./.trusted checkout"

    sparse = trusted_checkouts[0]["with"]["sparse-checkout"]
    assert ".github/scripts" in sparse
    assert ".github/actions" in sparse


def test_trusted_checkout_runs_before_anything_untrusted(workflow: dict):
    """The gate cannot be read from a tree the pull request wrote. The trusted
    checkout must land before the authorization step and before the PR code."""
    steps = steps_of(workflow, "llm_evals")
    names = [step.get("name") for step in steps]

    trusted_idx = next(
        i
        for i, step in enumerate(steps)
        if (step.get("with") or {}).get("path") == ".trusted"
    )
    authz_idx = next(
        i for i, step in enumerate(steps) if "authorizeCommand" in script_of(step)
    )
    pr_code_idx = next(
        i
        for i, step in enumerate(steps)
        if (step.get("with") or {}).get("path") == "code"
    )

    assert (
        trusted_idx < authz_idx
    ), f"authorization runs before the trusted checkout: {names}"
    assert (
        trusted_idx < pr_code_idx
    ), f"PR code is checked out before the trusted checkout: {names}"


def test_trusted_checkout_is_unconditional(workflow: dict):
    """A skipped trusted checkout means `uses: ./.trusted/...` resolves to nothing;
    it must not be gated on a step that itself depends on it."""
    trusted_checkout = next(
        step
        for step in steps_of(workflow, "llm_evals")
        if (step.get("with") or {}).get("path") == ".trusted"
    )

    assert "if" not in trusted_checkout


def test_trusted_checkout_targets_a_base_repository_ref(workflow: dict):
    """`ref:` must resolve to a branch in the base repo, never a PR head."""
    trusted_checkout = next(
        step
        for step in steps_of(workflow, "llm_evals")
        if (step.get("with") or {}).get("path") == ".trusted"
    )

    ref = trusted_checkout["with"]["ref"]
    assert "pull_request.base.ref" in ref
    assert "head" not in ref
