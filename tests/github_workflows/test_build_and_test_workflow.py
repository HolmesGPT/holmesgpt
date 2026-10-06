"""Security regression tests for .github/workflows/build-and-test.yaml (ROB-1105).

The workflow's `check-changes` job decides whether the `test` and `build-binary`
jobs run, and the `build-and-test-gate` job is the required status check. A
pull-request author controls the branch name, so these tests run the real step
scripts under bash, with `${{ }}` expressions expanded textually the way GitHub
expands them, and check that a crafted branch name can neither run commands
nor skip the tests.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS_DIR / "build-and-test.yaml"
BASE_REPO = "HolmesGPT/holmesgpt"

EXPRESSION_RE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")

# Contexts any pull-request or issue author can set to an arbitrary string.
# Expanding one inside a `run:` script lets that author inject shell.
UNTRUSTED_CONTEXT_RE = re.compile(
    r"github\.head_ref"
    r"|github\.event\.pull_request\.(title|body|head\.ref|head\.label)"
    r"|github\.event\.(issue|discussion)\.(title|body)"
    r"|github\.event\.(comment|review|review_comment)\.body"
    r"|github\.event\.(head_commit|commits)"
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git",
)


def _load_workflow(path: Optional[Path] = None) -> dict:
    return yaml.safe_load((path or WORKFLOW).read_text())


def _render(text: str, context: Dict[str, str]) -> str:
    """Expand `${{ expr }}` by plain text substitution, as GitHub does."""

    def substitute(match: "re.Match[str]") -> str:
        expression = match.group(1)
        if expression not in context:
            raise KeyError(f"test context is missing a value for ${{{{ {expression} }}}}")
        return context[expression]

    return EXPRESSION_RE.sub(substitute, text)


def _run_step(
    step: dict, context: Dict[str, str], cwd: Path, tmp_path: Path
) -> "tuple[subprocess.CompletedProcess, Dict[str, str]]":
    script = _render(step["run"], context)
    output_file = tmp_path / "github_output"
    output_file.write_text("")
    env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output_file)}
    for name, value in (step.get("env") or {}).items():
        env[name] = _render(str(value), context)
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    outputs: Dict[str, str] = {}
    for line in output_file.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value  # last write wins, like GitHub
    return result, outputs


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def pr_checkout(tmp_path: Path):
    """A git repo with a base commit; call it with a file path to make the PR commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    def make_pr(changed_file: str) -> Dict[str, str]:
        path = repo / changed_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("change\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "pr")
        return {"base_sha": base_sha, "head_sha": _git(repo, "rev-parse", "HEAD")}

    return repo, make_pr


def _check_changes_step() -> dict:
    steps = _load_workflow()["jobs"]["check-changes"]["steps"]
    return next(s for s in steps if s.get("id") == "changes")


def _pr_context(
    head_ref: str, head_repo: str, shas: Dict[str, str]
) -> Dict[str, str]:
    return {
        "github.head_ref": head_ref,
        "github.event.pull_request.head.repo.full_name": head_repo,
        "github.repository": BASE_REPO,
        "github.event_name": "pull_request",
        "github.event.pull_request.base.sha": shas["base_sha"],
        "github.sha": shas["head_sha"],
    }


def _run_check_changes(
    tmp_path: Path, repo: Path, head_ref: str, head_repo: str, shas: Dict[str, str]
):
    return _run_step(
        _check_changes_step(), _pr_context(head_ref, head_repo, shas), repo, tmp_path
    )


# Every entry is a legal git branch name (no spaces, so ${IFS} stands in).
INJECTION_BRANCH_NAMES = [
    "feature$(touch${IFS}PWNED)",
    "feature`touch${IFS}PWNED`",
    'x"$(touch${IFS}PWNED)"',
    'x";touch${IFS}PWNED;"',
    'x";echo${IFS}should_test=false>>$GITHUB_OUTPUT;touch${IFS}PWNED;"',
]


@pytest.mark.parametrize("head_ref", INJECTION_BRANCH_NAMES)
def test_injection_payloads_are_legal_branch_names(head_ref):
    subprocess.run(["git", "check-ref-format", "--branch", head_ref], check=True)


@pytest.mark.parametrize("head_ref", INJECTION_BRANCH_NAMES)
def test_branch_name_is_not_executed(tmp_path, pr_checkout, head_ref):
    repo, make_pr = pr_checkout
    shas = make_pr("holmes/core/something.py")

    result, outputs = _run_check_changes(
        tmp_path, repo, head_ref, "attacker/holmesgpt", shas
    )

    assert not (repo / "PWNED").exists(), "branch name was executed as shell"
    assert result.returncode == 0, result.stderr
    assert outputs.get("should_test") == "true"


@pytest.mark.parametrize(
    "head_ref", ["automated/benchmark-20260101_000000", "automated/benchmark-x"]
)
def test_fork_cannot_skip_tests_with_benchmark_branch_name(
    tmp_path, pr_checkout, head_ref
):
    repo, make_pr = pr_checkout
    shas = make_pr("holmes/core/tool_calling_llm.py")

    result, outputs = _run_check_changes(
        tmp_path, repo, head_ref, "attacker/holmesgpt", shas
    )

    assert result.returncode == 0, result.stderr
    assert outputs.get("should_test") == "true"


def test_same_repo_benchmark_branch_still_skips_tests(tmp_path, pr_checkout):
    repo, make_pr = pr_checkout
    shas = make_pr("docs/development/evaluations/results.json")

    result, outputs = _run_check_changes(
        tmp_path, repo, "automated/benchmark-20260101_000000", BASE_REPO, shas
    )

    assert result.returncode == 0, result.stderr
    assert outputs.get("should_test") == "false"


@pytest.mark.parametrize(
    "changed_file,expected",
    [
        ("docs/index.md", "false"),
        ("CONTRIBUTING.md", "false"),
        ("mkdocs.yml", "false"),
        ("docs/reference/http-api.md", "true"),
        ("holmes/main.py", "true"),
    ],
)
def test_docs_only_changes_skip_tests(tmp_path, pr_checkout, changed_file, expected):
    repo, make_pr = pr_checkout
    shas = make_pr(changed_file)

    result, outputs = _run_check_changes(
        tmp_path, repo, "some-feature", "attacker/holmesgpt", shas
    )

    assert result.returncode == 0, result.stderr
    assert outputs.get("should_test") == expected


def _run_gate(
    tmp_path: Path, check_changes: str, should_test: str, test: str, build: str
) -> int:
    steps = _load_workflow()["jobs"]["build-and-test-gate"]["steps"]
    context = {
        "needs.check-changes.result": check_changes,
        "needs.check-changes.outputs.should_test": should_test,
        "needs.test.result": test,
        "needs.build-binary.result": build,
    }
    result, _ = _run_step(steps[0], context, tmp_path, tmp_path)
    return result.returncode


@pytest.mark.parametrize(
    "check_changes,should_test,test,build,passes",
    [
        ("success", "true", "success", "success", True),
        ("success", "false", "skipped", "skipped", True),
        ("success", "true", "skipped", "success", False),
        ("success", "true", "success", "skipped", False),
        ("success", "true", "failure", "success", False),
        ("success", "true", "cancelled", "success", False),
        ("failure", "", "skipped", "skipped", False),
        # should_test missing or garbled must not count as a docs-only skip
        ("success", "", "skipped", "skipped", False),
        ("success", "maybe", "skipped", "skipped", False),
        # a docs-only verdict with test jobs that ran but failed is not a pass
        ("success", "false", "failure", "skipped", False),
    ],
)
def test_gate(tmp_path, check_changes, should_test, test, build, passes):
    assert (_run_gate(tmp_path, check_changes, should_test, test, build) == 0) is passes


def _run_scripts(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from _run_scripts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _run_scripts(item)


@pytest.mark.parametrize(
    "workflow", sorted(WORKFLOWS_DIR.glob("*.y*ml")), ids=lambda p: p.name
)
def test_no_untrusted_expression_inside_run_scripts(workflow):
    offending = [
        expression
        for script in _run_scripts(_load_workflow(workflow))
        for expression in EXPRESSION_RE.findall(script)
        if UNTRUSTED_CONTEXT_RE.search(expression)
    ]
    assert not offending, (
        f"{workflow.name} expands attacker-controlled values inside a run: script: "
        f"{offending}. Pass them through env: and reference the shell variable."
    )
