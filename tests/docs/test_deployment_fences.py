import re
import subprocess
import sys
from pathlib import Path

import markdown
import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config

from docs import custom_fences, fence_checks
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
    """The preprocessor and the fence_checks hook raise on a fence in any form they do not support."""
    monkeypatch.chdir(REPO)  # pymdownx.snippets resolves base_path from the cwd
    page = path.relative_to(DOCS).as_posix()
    configs = {**site_config["mdx_configs"]}
    configs["docs.custom_fences"] = {"page": page}
    md = markdown.Markdown(
        extensions=site_config["markdown_extensions"], extension_configs=configs
    )
    assert md.convert(path.read_text())
    fence_checks.check_page(path.read_text(), page)


# The holmes modules a process has imported, and whether it has the fence module.
LOADED = (
    "import sys; print(sorted(name for name in sys.modules if name.split('.')[0] == 'holmes'),"
    " 'docs.custom_fences' in sys.modules)"
)


@pytest.mark.parametrize(
    "load",
    [
        "import docs.custom_fences",
        # A tool that expands the fences without Holmes loads the config without its hooks.
        "from mkdocs.config import load_config; load_config('mkdocs.yml', hooks=[])",
    ],
    ids=["the fence module", "mkdocs.yml without its hooks"],
)
def test_expanding_the_fences_imports_nothing_from_holmes(load):
    result = subprocess.run(
        [sys.executable, "-c", f"{load}\n{LOADED}"], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[] True"


def test_two_builds_of_the_site_are_byte_identical(tmp_path):
    """Each build also runs the on_post_page check over every page."""
    sites = [tmp_path / "first", tmp_path / "second"]
    for site in sites:
        build(load_config(str(REPO / "mkdocs.yml"), site_dir=str(site)))
    files = [sorted(path.relative_to(site) for path in site.rglob("*") if path.is_file()) for site in sites]
    assert files[0] == files[1]
    assert [path for path in files[0] if (sites[0] / path).read_bytes() != (sites[1] / path).read_bytes()] == []


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
        "```yaml-toolset-config\ntoolsets:\n  newrelic:\n    enabled: true\n---\n```\n",
    ],
    ids=["empty", "comment-only", "comment-only-values", "comment-only-fields", "empty-fields"],
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


def build_page(tmp_path, text):
    """Build a site of one page, docs/index.md, with mkdocs.yml's pipeline."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(f"# Page\n\n{text}")
    build(load_config(str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")))


@pytest.mark.parametrize(
    "values, path",
    [
        ("mcpAddons:\n  aws:\n    enabeld: true\n", "mcpAddons.aws.enabeld"),
        ("mcpAddons:\n  aws:\n    enabled: true\n    config:\n      regoin: us-east-1\n", "mcpAddons.aws.config.regoin"),
        ("mcpAddons:\n  aws:\n    image:\n      tag: x\n", "mcpAddons.aws.image.tag"),
        ("mcpAddons:\n  aws:\n    nodeSelector:\n      kubernetes.io/os: linux\n", "mcpAddons.aws.nodeSelector.kubernetes.io/os"),
    ],
    ids=["addon-key", "nested-key", "under-a-scalar", "free-form-map-no-page-fills"],
)
def test_an_mcp_addon_value_the_holmes_chart_has_no_key_for_fails_the_build(tmp_path, monkeypatch, values, path):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"^index\.md:3: `{re.escape(path)}` is not a value of the Holmes chart"):
        build_page(tmp_path, f"```yaml-helm-values\n{values}```\n")


def test_the_cli_tab_keys_are_the_chart_values_that_are_holmes_config(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(custom_fences, "CLI_CONFIG_KEYS", frozenset({"toolsets"}))
    with pytest.raises(TabFenceError, match=r"^docs/custom_fences\.py: CLI_CONFIG_KEYS is \['toolsets'\]"):
        build_page(tmp_path, "Text.\n")


INCLUDE = '--8<-- "snippets/toolsets_that_provide_logging.md"\n\n'
FRONT_MATTER = "---\ntitle: Page\n---\n"


@pytest.mark.parametrize(
    "text, line, error",
    [
        (f"# Page\n\n{INCLUDE}```yaml-helm-values\npodLabels:\n  app: holmes\n```\n", 5, "`podLabels` is not a value"),
        (f"# Page\n\n{INCLUDE}```multi-instance\ntoolset: x\n```\n", 5, "unsupported form"),
        (f"{FRONT_MATTER}# Page\n\n```yaml-helm-values\npodLabels:\n  app: holmes\n```\n", 6, "`podLabels` is not a value"),
        (f"{FRONT_MATTER}# Page\n\n```multi-instance\ntoolset: x\n```\n", 6, "unsupported form"),
    ],
    ids=["include-hook", "include-preprocessor", "front-matter-hook", "front-matter-preprocessor"],
)
def test_a_fence_error_names_the_line_in_the_page_source(tmp_path, monkeypatch, text, line, error):
    """The hook raises a deployment fence's body errors before the Markdown pipeline
    runs; the preprocessor raises a multi-instance fence's."""
    monkeypatch.chdir(REPO)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(text)
    with pytest.raises(TabFenceError, match=rf"^index\.md:{line}: {error}"):
        build(load_config(str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")))


TOOLSET = "toolsets:\n  newrelic:\n    enabled: true\n    config:\n      nr_account_id: \"1\"\n"


@pytest.mark.parametrize(
    "fence",
    [
        f"```yaml-toolset-config\n{TOOLSET}---\ncli:\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\ntest:\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\nnamed-secrets: []\n```\n",
        "```yaml-helm-values\nmodelList:\n  gpt:\n    model: openai/gpt-4.1\n---\ndeployment-values:\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\nsecrets:\n  - --from-literal=X=y\n```\n",
        "```yaml-toolset-config\ntoolsets:\n```\n",
        "```yaml-toolset-config\ntoolsets: {}\n```\n",
        "```yaml-helm-values\nmodelList:\n```\n",
    ],
    ids=["cli", "test", "named-secrets", "deployment-values", "unknown-field", "toolsets", "toolsets-{}", "modelList"],
)
def test_a_field_or_value_written_with_no_value_or_outside_the_list_fails_the_build(tmp_path, monkeypatch, fence):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=r"^index\.md:3: unsupported form of a custom fence"):
        build_page(tmp_path, fence)


DATADOG = "toolsets:\n  datadog/general:\n    enabled: true\n    config:\n      api_key: \"{{ env.DD_API_KEY }}\"\n"


@pytest.mark.parametrize(
    "fence",
    [
        f"```yaml-toolset-config {{reuse}}\n{DATADOG}---\nsecret:\n  - --from-literal=DD_API_KEY=x\n```\n",
        f"```yaml-toolset-config {{reuse}}\n{DATADOG}---\nnamed-secrets:\n  - name: dd-ca\n    keys:\n      - --from-file=ca.crt=./ca.crt\n```\n",
        f"```yaml-toolset-config {{reuse}}\n{DATADOG}---\ntest: holmes toolset list\n```\n",
        "```yaml-helm-values {reuse}\nmodelList:\n  gpt:\n    api_key: \"{{ env.OPENAI_API_KEY }}\"\n---\ndeployment-values: [service-account]\n```\n",
    ],
    ids=["secret", "named-secrets", "test", "deployment-values"],
)
def test_a_reuse_fence_with_a_field_other_than_cli_fails_the_build(tmp_path, monkeypatch, fence):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=r"^index\.md:3: unsupported form of a custom fence"):
        build_page(tmp_path, fence)


@pytest.mark.parametrize("key", ["config", "podLabels", "extraVolumes", "customToolsets"])
def test_a_value_the_holmes_chart_has_no_key_for_fails_the_build(tmp_path, monkeypatch, key):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"^index\.md:3: `{key}` is not a value of the Holmes chart"):
        build_page(tmp_path, f"```yaml-helm-values\n{key}:\n  app: holmes\n```\n")


@pytest.mark.parametrize(
    "values, error",
    [
        (
            "toolsets:\n  grafana/dashboards:\n    enabled: true\n    config:\n      verify_ssl: true\n",
            r"`toolsets\.grafana/dashboards\.config` is not a config the toolset accepts: .*api_url",
        ),
        (
            "toolsets:\n  orders-db:\n    type: database\n    config:\n      read_only: true\n",
            r"`toolsets\.orders-db\.config` is not a config the toolset accepts: .*connection_url",
        ),
        (
            "toolsets:\n  prometheus/metrics:\n    enabled: true\n    subtype: prom\n    config:\n      prometheus_url: http://prometheus:9090\n",
            r"`toolsets\.prometheus/metrics\.subtype` names no config of the toolset",
        ),
        (
            "mcp_servers:\n  jenkins:\n    description: Jenkins\n    config:\n      mode: streamable-http\n",
            r"`mcp_servers\.jenkins\.config` is not a config the toolset accepts: .*url",
        ),
        (
            "toolsets:\n  prometheus/metrics:\n    enabled: true\n    config:\n      prometheus_url: http://prometheus:9090\n      timout: 10\n",
            r"`toolsets\.prometheus/metrics\.config` is not a config the toolset accepts: .*`timout` is not a field it declares",
        ),
        (
            "mcp_servers:\n  jira:\n    description: Jira\n    config:\n      url: https://mcp.example.com/mcp\n      oauth:\n        client_idd: holmes\n",
            r"`mcp_servers\.jira\.config` is not a config the toolset accepts: .*`oauth\.client_idd` is not a field it declares",
        ),
    ],
    ids=["built-in", "custom-named", "subtype", "mcp_servers", "undeclared-key", "undeclared-nested-key"],
)
def test_a_toolset_config_its_toolset_refuses_fails_the_build(tmp_path, monkeypatch, values, error):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"(?s)^index\.md:3: {error}"):
        build_page(tmp_path, f"```yaml-toolset-config\n{values}```\n")


@pytest.mark.parametrize(
    "values, error",
    [
        (
            "toolsets:\n  prometheus/metrics:\n    enabeld: true\n",
            r"`toolsets\.prometheus/metrics` is not in a form pages write for a built-in toolset: .*enabeld",
        ),
        (
            "toolsets:\n  orders-db:\n    type: databse\n    config:\n      connection_url: sqlite:///x.db\n",
            r"`toolsets\.orders-db` is not in a form pages write for a toolset with a `type:`: .*databse",
        ),
        (
            "toolsets:\n  prometheus/metric:\n    enabled: true\n",
            r"`toolsets\.prometheus/metric` is not in a form pages write for a YAML toolset .*tools",
        ),
        (
            "mcp_servers:\n  jenkins:\n    enabled: true\n    config:\n      url: http://jenkins:8080/mcp\n",
            r"`mcp_servers\.jenkins` is not in a form pages write for an MCP server: .*enabled",
        ),
    ],
    ids=["built-in", "type", "yaml-toolset", "mcp_servers"],
)
def test_a_toolset_block_in_a_form_no_page_writes_fails_the_build(tmp_path, monkeypatch, values, error):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"(?s)^index\.md:3: {error}"):
        build_page(tmp_path, f"```yaml-toolset-config\n{values}```\n")
