import re
from pathlib import Path
from typing import List

import yaml  # type: ignore

from docs.custom_fences import FENCE_LINE_RE

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
VALUES = yaml.safe_load((REPO / "helm" / "holmes" / "values.yaml").read_text())

INLINE_CODE_RE = re.compile(r"`([^`]+)`")
# The forms of a span that names a chart path: mapping keys joined by dots, optionally
# followed by `: <value>`.
CHART_PATH_RE = re.compile(r"(?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:: .+)?")

# Spans that start with a top-level key of the values but name something else.
NOT_CHART_PATHS = {
    "tls.crt": "a key of a Kubernetes TLS secret",
    "tls.key": "a key of a Kubernetes TLS secret",
    "toolsets.yaml": "a file name",
    "mcpAddons.mariadb": "an add-on the chart no longer has, which a page tells readers to delete from their values",
}


def unknown_chart_paths(text: str, page: str) -> List[str]:
    """An error for each inline code span outside a fence that starts with a top-level key of
    the Holmes chart's values and names a path the values do not have."""
    errors = []
    in_fence = False
    for number, line in enumerate(text.split("\n"), 1):
        if FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for span in INLINE_CODE_RE.findall(line):
            if re.split(r"[.:\[ =]", span, maxsplit=1)[0] not in VALUES or span in NOT_CHART_PATHS:
                continue
            match = CHART_PATH_RE.fullmatch(span)
            if match is None:
                errors.append(f"{page}:{number}: `{span}` starts with a chart key but is not a chart path in a form this check reads")
                continue
            node = VALUES
            for key in match["path"].split("."):
                if not (isinstance(node, dict) and key in node):
                    errors.append(f"{page}:{number}: `{span}`: helm/holmes/values.yaml has no `{match['path']}`")
                    break
                node = node[key]
    return errors


def test_every_chart_path_a_page_names_in_inline_code_is_in_the_chart_values():
    errors = []
    for path in sorted(DOCS.rglob("*.md")):
        errors += unknown_chart_paths(path.read_text(), path.relative_to(DOCS).as_posix())
    assert errors == []


def test_a_chart_path_the_values_lack_is_an_error_naming_the_page_and_line():
    text = (
        "Set `mcpAddons.aws.serviceAccount.name` and `logLevel: DEBUG`.\n"
        "```yaml\n`mcpAddons.aws.missing`\n```\n"
        "Then `mcpAddons.aws.serviceAccount.missing`, `toolsets.yaml` and `resources.limits[0]`.\n"
    )
    assert unknown_chart_paths(text, "page.md") == [
        "page.md:5: `mcpAddons.aws.serviceAccount.missing`: helm/holmes/values.yaml has no `mcpAddons.aws.serviceAccount.missing`",
        "page.md:5: `resources.limits[0]` starts with a chart key but is not a chart path in a form this check reads",
    ]
