from pathlib import Path

import markdown
import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config

from docs.custom_fences import FENCE_OPENING_RE, TabFenceError

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

PAGES_WITH_FENCES = sorted(
    path
    for path in DOCS.rglob("*.md")
    if any(FENCE_OPENING_RE.match(line) for line in path.read_text().split("\n"))
)


@pytest.fixture(scope="module")
def site_config():
    """The Markdown pipeline mkdocs.yml configures for every page."""
    return load_config(str(REPO / "mkdocs.yml"))


def test_there_are_pages_with_fences():
    assert PAGES_WITH_FENCES


@pytest.mark.parametrize(
    "path",
    PAGES_WITH_FENCES,
    ids=[str(path.relative_to(DOCS)) for path in PAGES_WITH_FENCES],
)
def test_every_fence_of_a_page_is_in_a_supported_form(site_config, monkeypatch, path):
    """The preprocessor raises on a fence in any form it does not support."""
    monkeypatch.chdir(REPO)  # pymdownx.snippets resolves base_path from the cwd
    configs = {**site_config["mdx_configs"]}
    configs["docs.custom_fences"] = {"page": str(path.relative_to(DOCS))}
    md = markdown.Markdown(
        extensions=site_config["markdown_extensions"], extension_configs=configs
    )
    assert md.convert(path.read_text())


def test_no_page_of_the_site_shows_a_fence_as_markdown(tmp_path):
    """The build runs the on_post_page check over every page."""
    build(load_config(str(REPO / "mkdocs.yml"), site_dir=str(tmp_path / "site")))


@pytest.mark.parametrize(
    "body",
    [
        "toolsets:\n  newrelic: [enabled\n",
        "toolsets:\n  newrelic:\n    enabled: true\n---\ncli: [\n",
    ],
    ids=["values", "fields"],
)
def test_a_fence_body_that_is_not_valid_yaml_fails_the_build(tmp_path, monkeypatch, body):
    monkeypatch.chdir(REPO)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(f"# Page\n\n```yaml-toolset-config\n{body}```\n")
    config = load_config(
        str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")
    )
    with pytest.raises(TabFenceError, match=r"^index\.md:3: the fence body is not valid YAML"):
        build(config)


@pytest.mark.parametrize(
    "fence",
    [
        "```multi-instance\n```\n",
        "```multi-instance\n# a comment\n```\n",
        "```yaml-toolset-config\n# a comment\n```\n",
        "```yaml-toolset-config\ntoolsets:\n  newrelic:\n    enabled: true\n---\n# a comment\n```\n",
    ],
    ids=["empty", "comment-only", "comment-only-values", "comment-only-fields"],
)
def test_an_empty_fence_body_fails_the_build_naming_the_fence(tmp_path, monkeypatch, fence):
    monkeypatch.chdir(REPO)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(f"# Page\n\n{fence}")
    config = load_config(
        str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")
    )
    with pytest.raises(TabFenceError, match=r"^index\.md:3: unsupported form of a custom fence"):
        build(config)
