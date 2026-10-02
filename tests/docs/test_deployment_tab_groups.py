"""Every deployment tab group on the model provider and data source pages keeps
one shape, so tools that read the pages can take the Kubernetes setup from the
Holmes Helm Chart tab.

A deployment tab group has only the tabs `Holmes CLI`, `Holmes Helm Chart` and
`Robusta Helm Chart`, in that order. Its Holmes Helm Chart tab holds, in order:
the service account line (when the page needs it), the secret step (when the
values read a secret), the values step, and the upgrade step with
`helm upgrade holmes robusta/holmes -f values.yaml`. Every deployment tab group
is a deployment fence, checked in the fence's expansion with its errors naming
the fence's line; a group written by hand in the page fails with one error
naming the page and line, and so does a `===` line that is not a `=== "<label>"`
tab. The values of every Holmes Helm Chart tab render through the chart.
test_deployment_fences.py checks that every fence renders.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from docs import custom_fences as cf

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
SECTIONS = ("ai-providers", "data-sources")

HOLMES_CLI = "Holmes CLI"
HOLMES_CHART = "Holmes Helm Chart"
ROBUSTA_CHART = "Robusta Helm Chart"
DEPLOYMENT_LABELS = (HOLMES_CLI, HOLMES_CHART, ROBUSTA_CHART)
# A label naming Holmes, Robusta or a Helm chart is meant as a deployment tab.
DEPLOYMENT_LIKE_RE = re.compile(r"\b(Holmes|Robusta|Helm)\b")

TAB_RE = re.compile(r'^(?P<indent> *)=== "(?P<label>[^"]*)"\s*$')
CODE_FENCE_RE = re.compile(r"^(?P<fence>`{3,})(?P<info>.*)$")

# The service account line, up to the name it states.
SERVICE_ACCOUNT_LINE = cf.SERVICE_ACCOUNT_LINE.partition("`")[0]
SECRET_COMMAND = "kubectl create secret generic "

# Robusta platform pages, which set up the platform rather than Holmes and are
# not written as deployment tab groups.
PLATFORM_PAGES = {"data-sources/cross-cluster-tools.md"}

PAGES = sorted(
    path
    for section in SECTIONS
    for path in (DOCS / section).rglob("*.md")
    if path.relative_to(DOCS).as_posix() not in PLATFORM_PAGES
)


class FenceError(Exception):
    """A code fence the check cannot skip reliably."""

    def __init__(self, line, message):
        super().__init__(message)
        self.line = line


def tab_groups(lines, offset=0):
    """Yield (line number, labels, [(label, first body line number, body)]) for
    every tab group, nested groups included."""
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("~~~"):
            raise FenceError(offset + i + 1, "a `~~~` code fence; use backticks")
        opening = cf.SUPPORTED_OPENING_RE.match(lines[i])
        if opening and not opening["region"]:
            # A custom fence ends at its own closing line; the code blocks of a
            # `cli` field inside it are indented, and are not its end.
            closing = next(
                (j for j in range(i + 1, len(lines)) if lines[j] == cf.CLOSING_LINE),
                None,
            )
            if closing is None:
                raise FenceError(offset + i + 1, "custom fence with no closing line")
            i = closing + 1
            continue
        fence = CODE_FENCE_RE.match(lines[i].strip())
        if fence:
            # Skip code blocks: a tab label inside one is not a tab.
            closing = next(
                (
                    j
                    for j in range(i + 1, len(lines))
                    if lines[j].strip() == fence["fence"]
                ),
                None,
            )
            if closing is None:
                raise FenceError(offset + i + 1, "code block with no closing fence of the same length")
            i = closing + 1
            continue
        match = TAB_RE.match(lines[i])
        if not match:
            if lines[i].lstrip().startswith("==="):
                raise FenceError(offset + i + 1, 'a `===` line that is not a `=== "<label>"` tab')
            i += 1
            continue
        indent = match["indent"]
        inner = indent + "    "
        start = i
        tabs = []
        while i < len(lines):
            match = TAB_RE.match(lines[i])
            if not match or match["indent"] != indent:
                break
            label, first = match["label"], i + 1
            i += 1
            body = []
            while i < len(lines) and (
                not lines[i].strip() or lines[i].startswith(inner)
            ):
                body.append(lines[i][len(inner) :])
                i += 1
            while body and not body[-1].strip():
                body.pop()
            tabs.append((label, offset + first + 1, body))
            while i < len(lines) and not lines[i].strip():
                i += 1
        yield offset + start + 1, [label for label, _, _ in tabs], tabs
        for _, first, body in tabs:
            yield from tab_groups(body, first - 1)


def holmes_tab_problems(first, body):
    """The ways a Holmes Helm Chart tab departs from its steps, as (line, message)."""
    problems = []
    steps = []
    expecting = None
    i = 0
    while i < len(body):
        line = body[i]
        number = first + i
        fence = CODE_FENCE_RE.match(line)
        if fence:
            closing = next(
                (j for j in range(i + 1, len(body)) if body[j] == fence["fence"]),
                None,
            )
            if closing is None:
                return problems + [(number, "code block with no closing fence")]
            code = "\n".join(body[i + 1 : closing])
            info = fence["info"].strip()
            if expecting == "secret":
                if info != "bash" or not code.startswith(SECRET_COMMAND):
                    problems.append((number, f"the secret step is not a `{SECRET_COMMAND.strip()}` bash block"))
                steps.append("secret")
            elif expecting == "values":
                if info != "yaml":
                    problems.append((number, "the values step is not a yaml block"))
                steps.append("values")
            elif expecting == "upgrade":
                if info != "bash" or code != cf.HOLMES_UPGRADE_COMMAND:
                    problems.append((number, f"the upgrade step is not `{cf.HOLMES_UPGRADE_COMMAND}`"))
                steps.append("upgrade")
            else:
                problems.append((number, "code block with no step caption above it"))
            expecting = None
            i = closing + 1
            continue
        text = line.strip()
        if text in (cf.SECRET_CAPTION, cf.SECRETS_CAPTION):
            expecting = "secret"
        elif text == cf.HOLMES_VALUES_CAPTION:
            expecting = "values"
        elif text == cf.APPLY_CAPTION:
            expecting = "upgrade"
        elif text and not (text.startswith(SERVICE_ACCOUNT_LINE) and not steps):
            problems.append((number, f"not a step of a Holmes Helm Chart tab: {text[:80]}"))
        i += 1
    order = [step for step in ("secret", "values", "upgrade") if step in steps]
    if steps != order:
        problems.append((first, f"steps out of order: {', '.join(steps)}"))
    for step in ("values", "upgrade"):
        if step not in steps:
            problems.append((first, f"no {step} step"))
    return problems


HAND_WRITTEN = (
    "a deployment tab group written by hand; write it as a "
    f"`{cf.TOOLSET_CONFIG_FENCE}` or `{cf.HELM_VALUES_FENCE}` fence"
)


def group_problems(lines, rendered):
    """The ways the tab groups in `lines` depart from the standard, as (line, message).
    A deployment tab group is allowed only in a fence's expansion (`rendered`)."""
    problems = []
    for number, labels, tabs in tab_groups(lines):
        deployment = [label for label in labels if label in DEPLOYMENT_LABELS]
        if not deployment:
            for label in labels:
                if DEPLOYMENT_LIKE_RE.search(label):
                    problems.append((number, f"tab label {label!r} is not one of {', '.join(DEPLOYMENT_LABELS)}"))
            continue
        if not rendered:
            problems.append((number, HAND_WRITTEN))
            continue
        if len(deployment) != len(labels):
            problems.append((number, f"deployment tabs mixed with other tabs: {labels}"))
            continue
        if labels != [label for label in DEPLOYMENT_LABELS if label in labels]:
            problems.append((number, f"tabs out of order: {labels}"))
        by_label = {label: (first, body) for label, first, body in tabs}
        if HOLMES_CHART not in by_label:
            if ROBUSTA_CHART in by_label:
                problems.append((number, "a Robusta Helm Chart tab with no Holmes Helm Chart tab"))
            continue
        problems += holmes_tab_problems(*by_label[HOLMES_CHART])
    return problems


def fence_expansions(rel, lines):
    """(line, markdown) of every deployment fence in `lines` that renders."""
    for i, line in enumerate(lines):
        opening = cf.SUPPORTED_OPENING_RE.match(line)
        if not opening or not opening["deployment"]:
            continue
        end = next((j for j in range(i + 1, len(lines)) if lines[j] == cf.CLOSING_LINE), None)
        if end is None:
            continue
        try:
            group = cf._deployment_section(opening, "\n".join(lines[i + 1 : end]).strip("\n"), rel)
        except cf.FenceBodyError:
            group = None
        if group is not None:
            yield i + 1, group


def page_problems(path):
    rel = path.relative_to(DOCS).as_posix()
    lines = path.read_text().split("\n")
    try:
        problems = [f"{rel}:{line}: {message}" for line, message in group_problems(lines, rendered=False)]
        for fence_line, group in fence_expansions(rel, lines):
            problems += [
                f"{rel}:{fence_line}: the fence renders, at line {line} of its expansion: {message}"
                for line, message in group_problems(group.split("\n"), rendered=True)
            ]
    except FenceError as e:
        return [f"{rel}:{e.line}: {e}"]
    return problems


def test_there_are_pages():
    assert PAGES


def test_every_platform_page_exists():
    """An entry whose page is gone is removed with the page."""
    assert [page for page in PLATFORM_PAGES if not (DOCS / page).exists()] == []


@pytest.mark.parametrize(
    "path", PAGES, ids=[str(path.relative_to(DOCS)) for path in PAGES]
)
def test_every_deployment_tab_group_has_the_standard_shape(path):
    problems = page_problems(path)
    assert not problems, "\n".join(problems)


def holmes_chart_values(path):
    """(fence line, values) of the Holmes Helm Chart tab of every deployment fence on the page."""
    rel = path.relative_to(DOCS).as_posix()
    for fence_line, group in fence_expansions(rel, path.read_text().split("\n")):
        for _, _, tabs in tab_groups(group.split("\n")):
            for label, _, body in tabs:
                if label != HOLMES_CHART:
                    continue
                opening = body.index("```yaml", body.index(cf.HOLMES_VALUES_CAPTION))
                yield fence_line, "\n".join(body[opening + 1 : body.index("```", opening + 1)])


HELM = shutil.which("helm")


@pytest.mark.skipif(HELM is None and not os.environ.get("CI"), reason="helm is not installed; CI runs this")
@pytest.mark.parametrize(
    "path", PAGES, ids=[str(path.relative_to(DOCS)) for path in PAGES]
)
def test_the_values_of_every_holmes_helm_chart_tab_render_through_the_chart(path, tmp_path):
    rel = path.relative_to(DOCS).as_posix()
    problems = []
    for fence_line, values in holmes_chart_values(path):
        values_file = tmp_path / f"{fence_line}.yaml"
        values_file.write_text(values)
        result = subprocess.run(
            ["helm", "template", "holmes", str(REPO / "helm" / "holmes"), "-f", str(values_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            problems.append(f"{rel}:{fence_line}: helm template fails: {result.stderr.strip()}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("opening", ["===+", "===!"])
def test_a_tab_written_in_another_form_fails_naming_the_line(opening):
    lines = ["Intro.", "", f'{opening} "Holmes CLI"', "", "    Run holmes."]
    with pytest.raises(FenceError, match=r'a `===` line that is not a `=== "<label>"` tab') as error:
        list(tab_groups(lines))
    assert error.value.line == 3
