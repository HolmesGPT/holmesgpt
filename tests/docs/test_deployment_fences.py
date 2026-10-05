import re
import subprocess
import sys
import time
from pathlib import Path

import markdown
import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config

from docs import custom_fences, fence_checks
from docs.custom_fences import SUPPORTED_OPENING_RE, TabFenceError

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

PAGES_WITH_FENCES = sorted(
    path
    for path in DOCS.rglob("*.md")
    if any(SUPPORTED_OPENING_RE.match(line) for line in path.read_text().split("\n"))
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
    page = path.relative_to(DOCS).as_posix()
    configs = {**site_config["mdx_configs"]}
    configs["docs.custom_fences"] = {"page": page}
    md = markdown.Markdown(
        extensions=site_config["markdown_extensions"], extension_configs=configs
    )
    assert md.convert(path.read_text())


# The holmes modules a process has imported, and whether it has the fence module.
LOADED = (
    "import sys; print(sorted(name for name in sys.modules if name.split('.')[0] == 'holmes'),"
    " 'docs.custom_fences' in sys.modules)"
)


@pytest.mark.parametrize(
    "load",
    [
        "import docs.custom_fences",
        "from mkdocs.config import load_config; load_config('mkdocs.yml')",
    ],
    ids=["the fence module", "mkdocs.yml"],
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
    "opening",
    [
        "``` {.yaml-toolset-config}",
        "```YAML-toolset-config",
        "```yaml-helm-values {secret-qualifier=bearer}",
        "```Robusta-Region",
        "```robusta-region {.yaml}",
        "```yaml-toolset-confg",
        "```Yaml",
        "~~~yaml",
        "```robusta-region {lang=python}",
    ],
    ids=["brace", "case", "helm-values-qualifier", "region-case", "region-attribute", "name", "language-case", "tilde", "region-language"],
)
def test_a_fence_opening_in_a_form_no_page_writes_fails_the_build(tmp_path, monkeypatch, opening):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"^index\.md:3: unsupported form of a fence: {re.escape(repr(opening))}"):
        build_page(tmp_path, f"{opening}\nmodelList:\n  gpt:\n    api_key: \"{{{{ env.OPENAI_API_KEY }}}}\"\n```\n")


INCLUDE = '--8<-- "snippets/toolsets_that_provide_logging.md"\n\n'
FRONT_MATTER = "---\ntitle: Page\n---\n"


@pytest.mark.parametrize(
    "text, line, error",
    [
        (f"# Page\n\n{INCLUDE}```yaml-helm-values\nmodelList:\n```\n", 5, "`modelList` has no value"),
        (f"# Page\n\n{INCLUDE}```multi-instance\ntoolset: x\n```\n", 5, "unsupported form"),
        (f"{FRONT_MATTER}# Page\n\n```yaml-helm-values\nmodelList:\n```\n", 6, "`modelList` has no value"),
        (f"{FRONT_MATTER}# Page\n\n```multi-instance\ntoolset: x\n```\n", 6, "unsupported form"),
    ],
    ids=["include-deployment", "include-multi-instance", "front-matter-deployment", "front-matter-multi-instance"],
)
def test_a_fence_error_names_the_line_in_the_page_source(tmp_path, monkeypatch, text, line, error):
    monkeypatch.chdir(REPO)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(text)
    with pytest.raises(TabFenceError, match=rf"^index\.md:{line}: {error}"):
        build(load_config(str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")))


@pytest.mark.parametrize(
    "fence",
    [
        "```yaml-toolset-config\ntoolsets:\n  newrelic:\n    enabled: true\n```\n",
        "```yaml-helm-values\nmodelList:\n  gpt:\n    model: openai/gpt-4.1\n```\n",
    ],
    ids=["yaml-toolset-config", "yaml-helm-values"],
)
def test_a_fence_in_a_snippet_file_fails_the_build_naming_the_file(tmp_path, monkeypatch, fence):
    monkeypatch.chdir(REPO)
    docs = tmp_path / "docs"
    (docs / "snippets").mkdir(parents=True)
    (docs / "index.md").write_text("# Page\n")
    (docs / "snippets" / "setup.md").write_text(f"Configure it:\n\n{fence}")
    # MkDocs loads the hook file as a module of its own, so the error is that module's TabFenceError.
    with pytest.raises(Exception, match=r"^snippets/setup\.md:3: unsupported form of a custom fence") as error:
        build(load_config(str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")))
    assert type(error.value).__name__ == "TabFenceError"


@pytest.mark.parametrize(
    "include",
    [
        '--8<-- "data-sources/builtin-toolsets/kafka.md"',
        "--8<--\nsnippets/toolset_refresh_warning.md\n--8<--",
        '--8<-- "snippets/toolsets_that_provide_loging.md"',
        '  --8<-- "snippets/toolset_refresh_warning.md"',
    ],
    ids=["outside-snippets", "block", "missing-snippet", "indented-outside-a-cli-field"],
)
def test_an_include_in_a_form_no_page_writes_fails_the_build(tmp_path, monkeypatch, include):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=r"^index\.md:3: unsupported form of an include: ' *--8<--"):
        build_page(tmp_path, f"{include}\n")


REFRESH_WARNING = '--8<-- "snippets/toolset_refresh_warning.md"'


@pytest.mark.parametrize(
    "block, line",
    [
        (f"```markdown\n{REFRESH_WARNING}\n```\n", 4),
        (f"```robusta-region\n{REFRESH_WARNING}\n```\n", 4),
        (f"```yaml-toolset-config\ntoolsets:\n  kubernetes/core:\n    enabled: true\n---\ncli: |\n  ```bash\n  {REFRESH_WARNING}\n  ```\n```\n", 10),
    ],
    ids=["code-block", "robusta-region", "code-block-in-a-cli-field"],
)
def test_an_include_inside_a_block_fails_the_build(tmp_path, monkeypatch, block, line):
    """pymdownx.snippets would expand it inside the block."""
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"^index\.md:{line}: unsupported form of an include: ' *--8<--"):
        build_page(tmp_path, block)


def test_an_include_in_a_snippet_file_in_a_form_no_page_writes_fails_the_build(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    docs = tmp_path / "docs"
    (docs / "snippets").mkdir(parents=True)
    (docs / "index.md").write_text("# Page\n")
    (docs / "snippets" / "setup.md").write_text('Configure it:\n\n--8<-- "data-sources/builtin-toolsets/kafka.md"\n')
    with pytest.raises(Exception, match=r"^snippets/setup\.md:3: unsupported form of an include") as error:
        build(load_config(str(REPO / "mkdocs.yml"), docs_dir=str(docs), site_dir=str(tmp_path / "site")))
    assert type(error.value).__name__ == "TabFenceError"


TOOLSET = "toolsets:\n  newrelic:\n    enabled: true\n    config:\n      nr_account_id: \"1\"\n"


@pytest.mark.parametrize(
    "fence",
    [
        f"```yaml-toolset-config\n{TOOLSET}---\ncli:\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\ntest:\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\nnamed-secrets: []\n```\n",
        "```yaml-helm-values\nmodelList:\n  gpt:\n    model: openai/gpt-4.1\n---\ndeployment-values:\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\nsecrets:\n  - --from-literal=X=y\n```\n",
        f"```yaml-toolset-config\n{TOOLSET}---\ntest: |\n  holmes toolset list\n  holmes ask hi\n```\n",
    ],
    ids=["cli", "test", "named-secrets", "deployment-values", "unknown-field", "multi-line-test"],
)
def test_a_field_written_with_no_value_or_outside_the_list_fails_the_build(tmp_path, monkeypatch, fence):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=r"^index\.md:3: unsupported form of a custom fence"):
        build_page(tmp_path, fence)


# A rule whose `apiGroups: [""]`, the core API group, is an empty string that stays accepted.
RULE = 'customClusterRoleRules:\n  - apiGroups: [""]\n    resources: ["pods"]\n    verbs:\n'


@pytest.mark.parametrize(
    "values, path",
    [
        ("toolsets:\n", "toolsets"),
        ("toolsets: {}\n", "toolsets"),
        ("toolsets:\n  newrelic:\n", "toolsets.newrelic"),
        ("modelList:\n", "modelList"),
        ("modelList:\n  gpt:\n    model: openai/gpt-4.1\n    api_key: \"\"\n", "modelList.gpt.api_key"),
        ("serviceAccount:\n  annotations: {}\n", "serviceAccount.annotations"),
        ("mcpAddons:\n  aws:\n    enabled: true\n    tolerations: []\n", "mcpAddons.aws.tolerations"),
        ("additionalEnvVars:\n  - name: TIMEOUT_SECONDS\n    value: \"\"\n", "additionalEnvVars[0].value"),
        (f"{RULE}      - get\n      -\n", "customClusterRoleRules[0].verbs[1]"),
        (f"{RULE}      - get\n      - null\n", "customClusterRoleRules[0].verbs[1]"),
        (f"{RULE}      - get\n  - {{}}\n", "customClusterRoleRules[1]"),
        (f"{RULE}      - get\n  - []\n", "customClusterRoleRules[1]"),
    ],
    ids=[
        "toolsets", "toolsets-{}", "toolset-block", "modelList", "free-form-map-value", "nested-{}", "nested-[]",
        "list-entry", "list-item", "list-item-null", "list-item-{}", "list-item-[]",
    ],
)
def test_a_value_written_with_no_value_fails_the_build(tmp_path, monkeypatch, values, path):
    monkeypatch.chdir(REPO)
    with pytest.raises(TabFenceError, match=rf"^index\.md:3: `{re.escape(path)}` has no value"):
        build_page(tmp_path, f"```yaml-toolset-config\n{values}```\n")


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




CHART = REPO / "helm" / "holmes"


@pytest.mark.parametrize(
    "path",
    PAGES_WITH_FENCES,
    ids=[str(path.relative_to(DOCS)) for path in PAGES_WITH_FENCES],
)
def test_the_chart_and_the_kubernetes_schemas_take_every_helm_tab_of_a_page(path):
    page = path.relative_to(DOCS).as_posix()
    errors = [
        f"{page}:{fence.line}: {error}"
        for fence in custom_fences.deployment_fences(path.read_text(), page)
        if fence.values
        for error in fence_checks.check_fence(fence.values, fence.environment, CHART)
    ]
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    "values, error",
    [
        ({"crdPermissions": {"argoo": True}}, r"`crdPermissions\.argoo`: the render does not depend on its value$"),
        (
            # Any non-empty string is truthy, so the chart's `if` takes it as true either way.
            {"namespaceScopedRBAC": "false"},
            r"`namespaceScopedRBAC`: the render does not depend on its value$",
        ),
        (
            {"additionalEnvVars": [{"value": "30"}]},
            r"the rendered Deployment holmes-holmes does not match its Kubernetes 1\.36 schema at `spec\.template\.spec\.containers\.0\.env\.\d+`: 'name' is a required property",
        ),
        ({"tls": {"enabled": True}}, r"helm template fails: .*tls\.enabled requires tls\.secretName"),
        (
            # Only strict mode refuses a field the schema does not declare.
            {"additionalVolumeMounts": [{"name": "x", "mountPath": "/x", "mountPth": "/x"}]},
            r"the rendered Deployment holmes-holmes does not match .*: Additional properties are not allowed \('mountPth' was unexpected\)",
        ),
        (
            # The addon's pod template carries a checksum of its config, which hashes the key too.
            {"mcpAddons": {"github": {"enabled": True, "auth": {"secretName": "s"}, "config": {"customCACert": {"enable": True}}}}},
            r"`mcpAddons\.github\.config\.customCACert\.enable`: the render does not depend on its value$",
        ),
        (
            # The addon's Secret holds a token the chart generates at random on each render.
            {"mcpAddons": {"kubernetesRemediation": {"enabled": True, "config": {"dcgmEnabeld": True}}}},
            r"`mcpAddons\.kubernetesRemediation\.config\.dcgmEnabeld`: the render does not depend on its value$",
        ),
        (
            # A template reads `clientId` only when `authMethod` is workload-identity or managed-identity.
            {"mcpAddons": {"azure": {"enabled": True, "config": {"authMethod": "service-principal", "clientId": "c"}}}},
            r"`mcpAddons\.azure\.config\.clientId`: the render does not depend on its value$",
        ),
    ],
    ids=[
        "unread-key", "string-for-a-bool", "kubernetes-schema", "helm-refuses", "undeclared-field",
        "unread-key-in-a-checksum", "unread-key-beside-a-random-token",
        "read-under-a-condition-the-values-do-not-meet",
    ],
)
def test_a_value_the_chart_or_kubernetes_refuses_is_an_error(values, error):
    errors = fence_checks.check_fence(values, {}, CHART)
    assert len(errors) == 1 and re.match(error, errors[0]), errors


def chart(directory: Path, template: str) -> Path:
    """A chart in `directory` with one template."""
    (directory / "templates").mkdir()
    (directory / "Chart.yaml").write_text("apiVersion: v2\nname: test\nversion: 0.1.0\n")
    (directory / "templates" / "object.yaml").write_text(template)
    return directory


def test_a_value_no_template_reads_is_an_error_beside_a_time_that_moves_on(tmp_path, monkeypatch):
    """Each render of a changed value starts a second after the render before it, so its time
    differs from that of every render of the fence's own values before it."""
    values = {"read": "x", "unread": "y"}
    time_and_read = chart(
        tmp_path,
        'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: holmes\n'
        'data:\n  rendered: {{ now | date "15:04:05" | quote }}\n  read: {{ .Values.read | quote }}\n',
    )
    render = fence_checks._render

    def a_second_later(changed, chart_dir):
        if changed != values:
            time.sleep(1.1)
        return render(changed, chart_dir)

    monkeypatch.setattr(fence_checks, "_render", a_second_later)
    assert fence_checks.check_fence(values, {}, time_and_read) == [
        "`unread`: the render does not depend on its value"
    ]


def test_values_whose_renders_differ_in_their_number_of_lines_are_an_error(tmp_path, monkeypatch):
    """The second render of the fence's own values has one more line, as a chart that writes a
    random number of lines can give it."""
    values = {"read": "x", "unread": "y"}
    read = chart(
        tmp_path, "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: holmes\ndata:\n  read: {{ .Values.read | quote }}\n"
    )
    render = fence_checks._render
    renders_of_values = 0

    def a_line_more_the_second_time(changed, chart_dir):
        nonlocal renders_of_values
        result = render(changed, chart_dir)
        if changed == values:
            renders_of_values += 1
            if renders_of_values == 2:
                result.stdout += "  line: line\n"
        return result

    monkeypatch.setattr(fence_checks, "_render", a_line_more_the_second_time)
    assert fence_checks.check_fence(values, {}, read) == [
        "two renders of these values differ in their number of lines, so which values the chart reads cannot be told"
    ]


def test_a_rendered_kind_with_no_kubernetes_schema_is_an_error(tmp_path):
    service_monitor = chart(
        tmp_path, "apiVersion: monitoring.coreos.com/v1\nkind: ServiceMonitor\nmetadata:\n  name: holmes\nspec: {}\n"
    )
    assert fence_checks.check_fence({}, {}, service_monitor) == [
        "the rendered ServiceMonitor holmes has no Kubernetes 1.36 schema in kubernetes-validate: "
        "kind ServiceMonitor, apiVersion monitoring.coreos.com/v1"
    ]


def test_the_fence_checks_without_helm_fail_saying_what_to_install(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match=r"need Helm on PATH: install Helm"):
        fence_checks.check_fence({}, {}, CHART)


def test_the_fence_checks_without_kubernetes_validate_fail_saying_what_to_install():
    hidden = "import sys; sys.modules['kubernetes_validate'] = None; import docs.fence_checks"
    result = subprocess.run([sys.executable, "-c", hidden], cwd=REPO, capture_output=True, text=True)
    assert result.returncode != 0
    assert "the fence checks need kubernetes-validate, a dev dependency: run `poetry install --with dev`" in result.stderr
