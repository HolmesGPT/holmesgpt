"""
Custom fences for the MkDocs documentation.

- yaml-toolset-config: a setting that applies to the CLI and to Kubernetes. Holmes CLI, Holmes Helm
  Chart and Robusta Helm Chart tabs.
- yaml-helm-values: a chart-only setting, with no CLI counterpart. Holmes Helm Chart and Robusta Helm
  Chart tabs.
- robusta-region: Creates 3 tabs (US, EU, AP) for any text containing api.robusta.dev, platform.robusta.dev, or
  sp.robusta.dev. Plain URLs render as code blocks; markdown links `[text](url)` render as clickable links.
- multi-instance: the standard "Multiple Instances" section for a toolset. The body is YAML with
  `toolset` (the toolset's config key), `name` (its display name) and `config` (a single-instance
  config example, a block scalar). It links to the Multiple Instances page with a path relative to
  the page.

robusta-region is a superfences custom fence, registered in mkdocs.yml. The other three expand into
markdown before any other fence or tab is rendered, so each renders exactly as the same markdown
written by hand, tab ids included. Each renders from its own body and options and the page's path,
and reads nothing else on the page. The two deployment fences render the deployment tab standard.

Supported forms. Write the fence at the start of a line, opened by three backticks and the fence
name, and closed by the first line of three backticks:

    ```yaml-toolset-config
    ```yaml-toolset-config {reuse}
    ```yaml-toolset-config {secret-qualifier=<name>}
    ```yaml-helm-values
    ```yaml-helm-values {reuse}
    ```multi-instance

A robusta-region fence opens at the start of a line or indented by four spaces, as
```` ```robusta-region ```` or ```` ```robusta-region {lang=<language>} ````, the language being
`yaml`, `bash` or `json`. A `multi-instance` body has `toolset`, `name` and `config`. Every other
fence is a code block, opened and closed by three backticks at any indent, its opening naming one
of `CODE_LANGUAGES` or none. Any other fence line (another info string or case, superfences'
`{.<name>}`, `~~~`, more backticks) fails the build with a message naming the page and the line,
and so do a body that is not valid YAML, a value that is not the chart's, and a page whose
rendered HTML shows a fence's markdown instead of its tabs (`on_post_page`). This module reads
only files and imports nothing from `holmes`; the checks that need Holmes, of each `toolsets` and
`mcp_servers` block, are the `docs/fence_checks.py` hook's.

The body of a deployment fence. The Holmes chart values, a block mapping whose first key starts at
the first column, then optionally a line `---` and the fields below, a second block mapping:

    modelList:
      gpt-4.1:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-4.1
    ---
    secret:
      - --from-literal=OPENAI_API_KEY="sk-..."
    cli: |
      ```bash
      export OPENAI_API_KEY="your-openai-api-key"
      holmes ask "what pods are failing?"
      ```

Each Helm tab shows the values (under `holmes:` in the Robusta tab) and the chart's upgrade command.
The values are written as the page shows them, comments included. Every key path of the values is
one of the chart's defaults in `helm/holmes/values.yaml`, and its value has the default's type,
except inside the maps of `FREE_FORM_VALUES`, whose keys are the reader's, and inside the blocks
`toolsets` and `mcp_servers` map each name to, which `docs/fence_checks.py` checks against Holmes.
A list's entries are in the form `LIST_ENTRIES` declares for it, and a list it declares none for
fails the build; an entry of a map of `FREE_FORM_ENTRIES` is in the form declared there.
No value at any key path is empty (null, `{}`, `[]` or `""`), and each block is a mapping. The
top-level keys that are also Holmes config (`CLI_CONFIG_KEYS`) are what a derived CLI tab shows.

Secrets. Every `{{ env.X }}` the values reference outside a comment line and set in no
`additionalEnvVars` entry is a key of the group's Kubernetes secret, in the order the values first
reference them. The secret is `holmes-<page file stem>`. The Helm tabs create it with
`kubectl create secret generic`, one `--from-literal=X=your-x` per key, and list it under
`extraEnvVarsSecrets`, which mounts each key as an env var; a derived CLI tab exports the same
variables.

`{secret-qualifier=<name>}` names the group's secret `holmes-<stem>-<name>`, for a group on the same
page that needs a secret with other keys. `<name>` is lowercase letters and digits, joined by `-`.

`{reuse}` is for a group whose secret an earlier group or step on the page creates: its Helm tabs
have no secret step, its values still list the secret, and a derived CLI tab still exports the keys.
Its only field, when it has one, is `cli`. The note naming the section that creates the secret is
written by hand above the fence:

    In Kubernetes, this reuses the `<secret>` secret created in the [<section>](#<anchor>) section above.

Above a yaml-helm-values fence, which has no CLI tab, it reads "Reuses the ...".

Fields, declared in `ToolsetConfigFields` and `HelmValuesFields`. Each is optional, and a field
that is written has a value.

- `secret`: `--from-literal=X=<value>` and `--from-file=X=<path>` arguments of the group's secret.
  One for a derived key sets the value the page shows; one for a key the values never reference as
  `{{ env.X }}` (a variable the tool reads from the environment) adds it, after the derived keys.
- `named-secrets`: secrets the values name themselves, which are not listed in
  `extraEnvVarsSecrets`: a file mounted through `additionalVolumes`, or a secret an addon reads by
  `secretName`. A list of `name` and `keys`, `keys` being arguments as in `secret`. Their commands
  follow the group's secret in the same secret step.
- `deployment-values` (yaml-helm-values only): `[service-account]`, or
  `[service-account, holmes-deployment]`. Each Helm tab opens with the line that states the
  chart's names for them, for the placeholders the page's steps use.
- `cli` (yaml-toolset-config only): the Holmes CLI tab's markdown, for a CLI setup that is a
  different procedure. Without it the CLI tab is derived: the exports, the values' Holmes config
  keys for ~/.holmes/config.yaml, and the refresh warning. A fence with `cli` and no values has the
  Holmes CLI tab alone, for a toolset that runs only in the CLI.
- `test` (a derived CLI tab only): a one-line command the CLI tab ends with, under "To test, run:".

Every custom fence is in a page's own source: one in a file under `docs/snippets/` fails the build
(`on_config`). The three this module expands are expanded before the includes, so in a snippet one
would render as a plain code block. An include, on a page or in a snippet, names a file that
exists under `docs/snippets/`, on a line of its own outside every code block and robusta-region
fence, unindented or, in a `cli` field, indented by two spaces:

    --8<-- "snippets/<file>.md"

Any other include fails the build, so every file a page includes is one `on_config` reads.

The page hook. Secrets are named after the page, and the multi-instance link is relative to it; the
page reaches the extension through this module's `on_page_markdown` MkDocs hook, so mkdocs.yml lists
this file under `hooks:`. An MkDocs config that sets its own `hooks:`, including one that INHERITs
mkdocs.yml (the child's list replaces the parent's), must list this file too, or every fence fails
the build.
"""

import html
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Dict, Iterator, List, NamedTuple, Optional, Tuple, Union

import yaml  # type: ignore
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

ROBUSTA_REGIONS = (("US", ""), ("EU", "eu"), ("AP", "ap"))
ROBUSTA_DOMAIN_RE = re.compile(r"\b(api|platform|sp)\.robusta\.dev\b")
MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)(\{[^}]*\})?$")


def _rewrite_robusta_domain(text: str, region_infix: str) -> str:
    """Rewrite api/platform/sp .robusta.dev to the regional variant."""
    if not region_infix:
        return text
    return ROBUSTA_DOMAIN_RE.sub(rf"\1.{region_infix}.robusta.dev", text)


def robusta_region_fence_format(source, language, css_class, options, md, **kwargs):
    """
    Render the source as three tabs (US, EU, AP), rewriting `api.robusta.dev`,
    `platform.robusta.dev` and `sp.robusta.dev` to the regional subdomain in each tab.

    Auto-detects two input shapes:

    1. A markdown link `[text](url)` (with optional `{...}` attribute list) →
       renders as a clickable link per region.
    2. Anything else → renders as a code block per region. Write `{lang=<name>}`
       after the fence name to set syntax highlighting (`yaml`, `bash` or `json`).

    Usage:

        ```robusta-region
        https://api.robusta.dev/litellm/model_prices_and_context_window.json
        ```

        ```robusta-region
        [platform.robusta.dev](https://platform.robusta.dev/)
        ```

        ```robusta-region {lang=yaml}
        holmes:
          additionalEnvVars:
            - name: ROBUSTA_API_ENDPOINT
              value: "https://api.robusta.dev"
        ```
    """
    inner = source.strip()
    # Inline `{lang=yaml}` attrs arrive via kwargs['attrs']; config-level options
    # come from mkdocs.yml (currently unused).
    attrs = kwargs.get("attrs") or {}
    inner_lang = attrs.get("lang") or (options or {}).get("lang") or ""
    lang_class_attr = (
        f' class="language-{html.escape(inner_lang)}"' if inner_lang else ""
    )

    link_match = MARKDOWN_LINK_RE.match(inner)

    # Markdown is built once per page, so the count numbers the page's groups and
    # two builds give the same ids; the prefix keeps them apart from tabbed's own.
    md.robusta_region_groups = getattr(md, "robusta_region_groups", 0) + 1
    group_name = f"__tabbed_robusta_region_{md.robusta_region_groups}"

    inputs_html = ""
    labels_html = ""
    blocks_html = ""

    for index, (region_name, region_infix) in enumerate(ROBUSTA_REGIONS, start=1):
        tab_id = f"{group_name}_{index}"
        checked_attr = ' checked="checked"' if index == 1 else ""
        inputs_html += (
            f'<input{checked_attr} id="{tab_id}" name="{group_name}" type="radio">\n'
        )
        labels_html += f'<label for="{tab_id}">{region_name}</label>\n'

        if link_match:
            link_text, link_url, _attrs = link_match.groups()
            regional_text = _rewrite_robusta_domain(link_text, region_infix)
            regional_url = _rewrite_robusta_domain(link_url, region_infix)
            inner_html = (
                f'<p><a href="{html.escape(regional_url)}">'
                f"{html.escape(regional_text)}</a></p>"
            )
        else:
            regional_content = _rewrite_robusta_domain(inner, region_infix)
            inner_html = (
                f"<pre><code{lang_class_attr}>{html.escape(regional_content)}"
                "</code></pre>"
            )

        blocks_html += f'<div class="tabbed-block">{inner_html}</div>\n'

    return (
        '<div class="tabbed-set" data-tabs="1:3">\n'
        f"{inputs_html}"
        f'<div class="tabbed-labels">\n{labels_html}</div>\n'
        f'<div class="tabbed-content">\n{blocks_html}</div>\n'
        "</div>"
    )


# The name mkdocs.yml lists this module under in `markdown_extensions`; the hook
# passes each page's path to the extension through this key of `mdx_configs`.
EXTENSION_NAME = "docs.custom_fences"
NO_PAGE = (
    f"but no page was given to the {EXTENSION_NAME} extension: "
    "list docs/custom_fences.py under hooks: in the MkDocs config"
)

TOOLSET_CONFIG_FENCE = "yaml-toolset-config"
HELM_VALUES_FENCE = "yaml-helm-values"
MULTI_INSTANCE_FENCE = "multi-instance"
# Rendered by superfences, as mkdocs.yml registers it; this module checks only its opening.
ROBUSTA_REGION_FENCE = "robusta-region"
# The page every multi-instance section links to, as a path under docs/.
MULTI_INSTANCE_PAGE = "data-sources/multi-instance-toolsets.md"

ENV_REFERENCE_RE = re.compile(r"\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# A line that opens or closes a fence, in any form ...
FENCE_LINE_RE = re.compile(r"^[ \t>]*(?:`{3,}|~{3,})")
# ... the languages of the code blocks pages write ...
CODE_LANGUAGES = (
    "bash",
    "dockerfile",
    "javascript",
    "json",
    "markdown",
    "nginx",
    "powershell",
    "promql",
    "python",
    "sql",
    "text",
    "yaml",
)
CODE_FENCE_RE = re.compile(rf"^ *```(?:{'|'.join(CODE_LANGUAGES)})?$")
# ... and the custom fences, in the forms pages write. Only yaml-toolset-config fences take a
# secret qualifier.
SUPPORTED_OPENING_RE = re.compile(
    rf"^```(?P<multi>{MULTI_INSTANCE_FENCE})$"
    rf"|^```(?P<deployment>{TOOLSET_CONFIG_FENCE}|{HELM_VALUES_FENCE})(?: \{{(?P<option>reuse"
    rf"|(?<={TOOLSET_CONFIG_FENCE} \{{)secret-qualifier=(?P<qualifier>[a-z0-9]+(?:-[a-z0-9]+)*))\}})?$"
    rf"|^(?:    )?```(?P<region>{ROBUSTA_REGION_FENCE})(?: \{{lang=(?:yaml|bash|json)\}})?$"
)
CLOSING_LINE = "```"
# A line pymdownx.snippets reads as an include, in any form ...
INCLUDE_RE = re.compile(r"^[ \t>]*;*-+8<-+")
# ... and the form pages write.
SUPPORTED_INCLUDE_RE = re.compile(r'^(?P<indent> *)--8<-- "(?P<file>snippets/[a-z0-9_]+\.md)"$')
# The indent of an include in a deployment fence's `cli` field.
CLI_INCLUDE_INDENT = "  "
# The directory an include's path is relative to: pymdownx.snippets' base_path in mkdocs.yml.
SNIPPETS_BASE = Path(__file__).resolve().parent
# The line of a deployment fence body that ends the values and starts its fields.
FIELDS_SEPARATOR = "---"
# A key of a secret, as one argument of `kubectl create secret generic`.
SECRET_ARGUMENT_RE = re.compile(
    r"--from-(?P<kind>literal|file)=(?P<key>[A-Za-z0-9_.-]+)=(?P<value>\S.*)"
)
# The keys a fence's values may set: the Holmes chart's values.
CHART_VALUES = Path(__file__).resolve().parents[1] / "helm" / "holmes" / "values.yaml"
CHART_DEFAULTS = yaml.safe_load(CHART_VALUES.read_text())
CHART_KEYS = frozenset(CHART_DEFAULTS)
# The chart values that are also Holmes config, which a derived CLI tab shows. The
# docs/fence_checks.py hook fails the build when this is not the `holmes.config.Config`
# fields that are CHART_KEYS.
CLI_CONFIG_KEYS = frozenset({"toolsets", "mcp_servers"})
# The chart-specific names a Helm tab can state, and the lines that state them.
DEPLOYMENT_VALUES = {
    ("service-account",): ". Use it as `<service-account>` on this page.",
    ("service-account", "holmes-deployment"): (
        " in the deployment `{release}-holmes`. Use them as `<service-account>` and "
        "`<holmes-deployment>` on this page."
    ),
}
SERVICE_ACCOUNT_LINE = (
    "Holmes runs as the service account `{release}-holmes-service-account` (the chart's "
    "default; if you set `customServiceAccountName`, it runs as that name, and with "
    "`createServiceAccount: false`, as the namespace's `default` service account)"
)

HOLMES_VALUES_CAPTION = (
    "When using the **standalone Holmes Helm Chart**, update your `values.yaml`:"
)
ROBUSTA_VALUES_CAPTION = "When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:"
CLI_CONFIG_CAPTION = "Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:"
SECRET_CAPTION = "Create a Kubernetes secret in the namespace Holmes runs in:"
SECRETS_CAPTION = "Create the Kubernetes secrets in the namespace Holmes runs in:"
APPLY_CAPTION = "Apply the configuration:"
TEST_CAPTION = "To test, run:"
HOLMES_UPGRADE_COMMAND = "helm upgrade holmes robusta/holmes -f values.yaml"
ROBUSTA_UPGRADE_COMMAND = "helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>"
MULTI_INSTANCE_LEAD = "List each one under `instances:` with a unique `name`."
REFRESH_WARNING_INCLUDE = '--8<-- "snippets/toolset_refresh_warning.md"'


class TabFenceError(Exception):
    """A tab fence that cannot be rendered; raised from a preprocessor, it fails the build."""


class FenceBodyError(Exception):
    """A fence body that cannot be rendered, with the reason; the preprocessor
    adds the page and line."""


def _code_block(language: str, text: str) -> str:
    return f"```{language}\n{text}\n```"


def _indent(text: str, prefix: str) -> str:
    """Prefix every non-empty line."""
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


def _tab(label: str, elements: list) -> str:
    return f'=== "{label}"\n\n' + _indent("\n\n".join(elements), "    ")


def _secret_placeholder(key: str) -> str:
    """The value a reader replaces: `DATADOG_API_KEY` gives `your-datadog-api-key`."""
    return "your-" + key.lower().replace("_", "-")


def _load(text: str):
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FenceBodyError(f"the fence body is not valid YAML: {e}") from e


def _block_mapping(body: str):
    """The mapping `body` loads as, if it is a block mapping whose first key starts at
    the first column, below any comment lines, else None."""
    data = _load(body)
    first = next(
        (line for line in body.split("\n") if line.strip() and not line.startswith("#")), ""
    )
    return data if isinstance(data, dict) and re.match(r"[A-Za-z_]", first) else None


def _is_empty(value) -> bool:
    return value is None or value in ({}, [], "")


def _chart_value_error(values: dict, defaults: Optional[dict], path: tuple = ()) -> Optional[str]:
    """Why `values`, the mapping at `path`, are not the chart's: the first key path whose
    value is empty, that the chart's `defaults` lack, or whose value has a type other than
    the default's; a list whose entries are not in their LIST_ENTRIES form; or an entry of
    a free-form map not in its FREE_FORM_ENTRIES form. `defaults` is None inside a map of
    FREE_FORM_VALUES, whose keys are the reader's. The toolset blocks, which the hook
    checks, are each a mapping."""
    for key, value in values.items():
        here = path + (key,)
        name = ".".join(map(str, here))
        if _is_empty(value):
            return f"`{name}` has no value"
        if defaults is not None:
            if key not in defaults:
                return f"`{name}` is not a value of the Holmes chart (helm/holmes/values.yaml)"
            if type(value) is not type(defaults[key]):
                return (
                    f"`{name}` is a {type(value).__name__}, and the Holmes chart's default for it "
                    f"(helm/holmes/values.yaml) is a {type(defaults[key]).__name__}"
                )
        error = None
        if len(here) == 1 and key in CLI_CONFIG_KEYS:
            error = _toolset_blocks_error(value, name)
        elif isinstance(value, dict):
            inner = None if defaults is None or here in FREE_FORM_VALUES else defaults[key]
            error = _chart_value_error(value, inner, here)
        elif isinstance(value, list):
            error = _list_entries_error(value, here, name)
        if not error and path in FREE_FORM_ENTRIES:
            try:
                FREE_FORM_ENTRIES[path].model_validate(value)
            except ValidationError as e:
                error = f"`{name}` is an entry in a form no page writes: {e}"
        if error:
            return error
    return None


def _list_entries_error(entries: list, path: tuple, name: str) -> Optional[str]:
    if path not in LIST_ENTRIES:
        return f"`{name}` is a list no page writes: LIST_ENTRIES in docs/custom_fences.py declares those pages write"
    try:
        TypeAdapter(List[LIST_ENTRIES[path]], config=ConfigDict(strict=True)).validate_python(entries)
    except ValidationError as e:
        return f"`{name}` holds an entry in a form no page writes: {e}"
    return None


def _toolset_blocks_error(blocks: dict, name: str) -> Optional[str]:
    for block_name, block in blocks.items():
        if _is_empty(block):
            return f"`{name}.{block_name}` has no value"
        if not isinstance(block, dict):
            return f"`{name}.{block_name}` is a {type(block).__name__}, and a block of `{name}` is a mapping"
    return None


def _multi_instance_section(body: str, page: str):
    """The standard "Multiple Instances" section for the toolset `body` names, or
    None if the body is not a supported form: its config example nested under
    `instances:` twice, the tools multiple instances add, and a link to the
    Multiple Instances page."""
    spec = _block_mapping(body)
    if spec is None or set(spec) != {"toolset", "name", "config"}:
        return None
    toolset, name, config = spec["toolset"], spec["name"], spec["config"]
    if not all(isinstance(value, str) and value.strip() for value in spec.values()):
        return None
    config = config.strip()
    # The wrapper names the discovery tool by replacing '/' with '_' in the toolset name.
    list_tool = toolset.replace("/", "_") + "_list_instances"
    # `config` is a YAML block scalar, which YAML has already dedented.
    fields = _indent(config, " " * 10)
    example = (
        f"toolsets:\n  {toolset}:\n    enabled: true\n    config:\n      instances:\n"
        f"        - name: prod\n{fields}\n        - name: staging\n{fields}"
    )
    link = posixpath.relpath(MULTI_INSTANCE_PAGE, posixpath.dirname(page))
    return "\n\n".join(
        [
            f"The {name} toolset can connect to more than one {name} instance. "
            f"{MULTI_INSTANCE_LEAD} Any config field "
            "set outside `instances:` becomes a default that every instance inherits, "
            "so shared settings only need to be written once.",
            _code_block("yaml", example),
            "When more than one instance is configured, HolmesGPT automatically adds "
            f"an `instance` parameter to every {name} tool (so it can pick which "
            f"instance to query) and a `{list_tool}` tool to list the configured "
            "instances. With a single instance — including the flat config without "
            "`instances:` — the tools are unchanged and fully backwards compatible.",
            f"See [Multiple Instances]({link}) for the full behaviour, including "
            "global defaults and health reporting.",
        ]
    )


def _secret_keys(arguments) -> Dict[str, Tuple[str, str]]:
    """{key: (kind, value)} for a list of `--from-literal=K=V` / `--from-file=K=PATH`
    arguments, in their order."""
    if not isinstance(arguments, list) or not arguments:
        raise ValueError("a secret's keys are a list of --from-literal / --from-file arguments")
    keys: Dict[str, Tuple[str, str]] = {}
    for argument in arguments:
        match = SECRET_ARGUMENT_RE.fullmatch(argument) if isinstance(argument, str) else None
        if match is None or match["key"] in keys:
            raise ValueError(f"not a --from-literal=KEY=VALUE or --from-file=KEY=PATH argument of a new key: {argument!r}")
        keys[match["key"]] = (match["kind"], match["value"])
    return keys


SecretKeys = Annotated[Dict[str, Tuple[str, str]], BeforeValidator(_secret_keys)]
Text = Annotated[str, Field(pattern=r"\S")]
# Text on one line, and the line break a block scalar ends it with.
Line = Annotated[str, Field(pattern=r"^[^\n]*\S[^\n]*\n?$")]
Mapping = Annotated[Dict[str, Any], Field(min_length=1)]


class Form(BaseModel):
    """A part of a fence body that holds only the fields pages write, each with a
    value of its type."""

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def every_field_has_a_value(cls, data):
        # `None` stands for an absent field, so a field written with no value is refused here.
        if isinstance(data, dict) and None in data.values():
            raise ValueError("a field with no value")
        return data


class NamedSecret(Form):
    name: Text
    keys: SecretKeys


# The chart values whose default is an empty map, which pages fill with keys of their own.
FREE_FORM_VALUES = frozenset(
    {
        ("modelList",),
        ("commonLabels",),
        ("serviceAccount", "annotations"),
        ("mcpAddons", "aws", "multiAccount", "profiles"),
        ("mcpAddons", "aws", "serviceAccount", "annotations"),
        ("mcpAddons", "azure", "serviceAccount", "annotations"),
        ("mcpAddons", "gcp", "serviceAccount", "annotations"),
        ("mcpAddons", "kubernetes", "config", "oauth"),
    }
)


class ModelListEntry(Form):
    model: Text
    api_key: Optional[Text] = None
    api_base: Optional[Text] = None
    api_version: Optional[Text] = None
    temperature: Optional[float] = None
    reasoning_effort: Optional[Text] = None
    thinking: Optional[Mapping] = None
    custom_args: Optional[Mapping] = None
    extra_headers: Optional[Mapping] = None
    aws_region_name: Optional[Text] = None
    aws_access_key_id: Optional[Text] = None
    aws_secret_access_key: Optional[Text] = None
    vertex_project: Optional[Text] = None
    vertex_location: Optional[Text] = None
    input_cost_per_token: Optional[float] = None
    output_cost_per_token: Optional[float] = None


# The maps of FREE_FORM_VALUES whose entries pages write in one form, and that form.
FREE_FORM_ENTRIES = {("modelList",): ModelListEntry}


class EnvVar(Form):
    name: Text
    value: Text


class SecretItem(Form):
    key: Text
    path: Text


class SecretVolumeSource(Form):
    secretName: Text
    items: Optional[Annotated[List[SecretItem], Field(min_length=1)]] = None


class SecretVolume(Form):
    name: Text
    secret: SecretVolumeSource


class HostPath(Form):
    path: Text
    type: Text


class HostPathVolume(Form):
    name: Text
    hostPath: HostPath


class VolumeMount(Form):
    name: Text
    mountPath: Text
    readOnly: bool


class ClusterRoleRule(Form):
    # "" is the core API group.
    apiGroups: Annotated[List[str], Field(min_length=1)]
    resources: Annotated[List[Text], Field(min_length=1)]
    verbs: Annotated[List[Text], Field(min_length=1)]


# The chart values that are lists, and the form of each entry pages write in them.
LIST_ENTRIES = {
    ("additionalEnvVars",): EnvVar,
    ("additionalVolumes",): Union[SecretVolume, HostPathVolume],
    ("additionalVolumeMounts",): VolumeMount,
    ("customClusterRoleRules",): ClusterRoleRule,
    ("mcpAddons", "kubernetes", "config", "extraArgs"): Text,
}


class Fields(Form):
    """The fields part of a deployment fence body: the fields the docstring lists
    for the fence."""

    secret: Optional[SecretKeys] = None
    named_secrets: Optional[Annotated[List[NamedSecret], Field(min_length=1)]] = Field(
        default=None, alias="named-secrets"
    )


class ToolsetConfigFields(Fields):
    cli: Optional[Text] = None
    test: Optional[Line] = None

    @model_validator(mode="after")
    def test_ends_a_derived_cli_tab(self):
        if self.cli is not None and self.test is not None:
            raise ValueError("`test` ends a derived CLI tab, and `cli` writes the tab")
        return self


class HelmValuesFields(Fields):
    deployment_values: Optional[Tuple[str, ...]] = Field(default=None, alias="deployment-values")

    @field_validator("deployment_values", mode="before")
    @classmethod
    def deployment_values_are_known(cls, value):
        if not isinstance(value, list) or tuple(value) not in DEPLOYMENT_VALUES:
            raise ValueError(f"one of {[list(names) for names in DEPLOYMENT_VALUES]}")
        return tuple(value)


def _secret_command(name: str, keys: dict) -> str:
    return " \\\n".join(
        [f"kubectl create secret generic {name}"]
        + [f"  --from-{kind}={key}={value}" for key, (kind, value) in keys.items()]
        + ["  -n <namespace>"]
    )


def _export(key: str, kind: str, value: str) -> str:
    return f"export {key}={value}" if kind == "literal" else f'export {key}="$(cat {value})"'


def _cli_config(values_text: str) -> str:
    """The Holmes config part of the values: each top-level CLI_CONFIG_KEYS key with
    the comment lines directly above it, in the values' order."""
    sections: list = []
    for line in values_text.split("\n"):
        key = re.match(r"([A-Za-z_][A-Za-z0-9_]*):", line)
        if key or not sections:
            # Comment lines directly above a key belong to it.
            comments: list = []
            while sections and sections[-1][1] and sections[-1][1][-1].startswith("#"):
                comments.insert(0, sections[-1][1].pop())
            sections.append((key[1] if key else None, comments + [line]))
        else:
            sections[-1][1].append(line)
    return "\n\n".join(
        "\n".join(lines).strip("\n") for key, lines in sections if key in CLI_CONFIG_KEYS
    )


def _environment_keys(values_text: str, values: dict, given: dict) -> dict:
    """{key: (kind, value)} of the group's env secret: every `{{ env.X }}` the values
    reference outside a comment and set in no `additionalEnvVars` entry, in the order
    the values first reference them, then the keys `given` adds; a key `given` names
    takes its value from there, any other key the placeholder value."""
    plain = {entry["name"] for entry in values.get("additionalEnvVars", [])}
    code = "\n".join(
        line for line in values_text.split("\n") if not line.lstrip().startswith("#")
    )
    referenced = [key for key in dict.fromkeys(ENV_REFERENCE_RE.findall(code)) if key not in plain]
    keys = {key: given.get(key, ("literal", _secret_placeholder(key))) for key in referenced}
    keys.update({key: argument for key, argument in given.items() if key not in keys})
    return keys


class DeploymentBody(NamedTuple):
    """A deployment fence body in a supported form."""

    values_text: str
    values: dict
    fields: Fields
    # The group's env secret, "" for none, and its keys as `_environment_keys` gives them.
    secret: str
    keys: dict
    # The variables the group gives Holmes: its secret's keys and its `additionalEnvVars`.
    environment: Dict[str, str]

    @property
    def chart_values_text(self) -> str:
        """The values the Helm tabs show: the page's, listing the group's secret first."""
        if not self.secret:
            return self.values_text
        return f"extraEnvVarsSecrets:\n  - {self.secret}\n\n{self.values_text}"


def _deployment_body(opening, body: str, page: str) -> Optional[DeploymentBody]:
    """A deployment fence body parsed and checked, or None if it is not a supported form."""
    lines = body.split("\n")
    split = lines.index(FIELDS_SEPARATOR) if FIELDS_SEPARATOR in lines else len(lines)
    values_text = "\n".join(lines[:split]).strip("\n")
    fields_text = "\n".join(lines[split + 1 :]).strip("\n")
    values = _block_mapping(values_text) if values_text else {}
    fields_data = _block_mapping(fields_text) if split < len(lines) else {}
    if values is None or fields_data is None:
        return None
    toolset_config = opening["deployment"] == TOOLSET_CONFIG_FENCE
    try:
        fields = (ToolsetConfigFields if toolset_config else HelmValuesFields).model_validate(fields_data)
    except ValidationError:
        return None
    if opening["option"] == "reuse" and fields.model_fields_set - {"cli"}:
        return None
    error = _chart_value_error(values, CHART_DEFAULTS)
    if error:
        raise FenceBodyError(error)

    if not values_text:
        # A setting with no Kubernetes counterpart: the Holmes CLI tab alone.
        if not isinstance(fields, ToolsetConfigFields) or opening["option"] or fields.model_fields_set != {"cli"}:
            return None
        return DeploymentBody(values_text, values, fields, "", {}, {})

    keys = _environment_keys(values_text, values, fields.secret or {})
    secret = ""
    if keys:
        secret = f"holmes-{PurePosixPath(page).stem}"
        if opening["qualifier"]:
            secret += f"-{opening['qualifier']}"
    elif opening["option"]:
        return None
    environment = {key: "value" for key in keys}
    environment.update({entry["name"]: entry["value"] for entry in values.get("additionalEnvVars", [])})
    return DeploymentBody(values_text, values, fields, secret, keys, environment)


def _deployment_section(opening, body: str, page: str):
    """The tab group of the deployment tab standard for a deployment fence body,
    or None if the body is not a supported form."""
    parsed = _deployment_body(opening, body, page)
    if parsed is None:
        return None
    _, _, fields, secret, keys, _ = parsed
    toolset_config = isinstance(fields, ToolsetConfigFields)
    cli = fields.cli if isinstance(fields, ToolsetConfigFields) else None
    test = fields.test if isinstance(fields, ToolsetConfigFields) else None
    if not parsed.values_text:
        return _tab("Holmes CLI", [cli.strip("\n")])

    commands = [_secret_command(secret, keys)] if keys and opening["option"] != "reuse" else []
    commands += [_secret_command(entry.name, entry.keys) for entry in fields.named_secrets or []]
    deployment_values = fields.deployment_values if isinstance(fields, HelmValuesFields) else None

    values_text = parsed.chart_values_text
    tabs = []
    if toolset_config:
        if cli is None:
            config = _cli_config(values_text)
            if not config:
                return None
            exports = []
            if keys:
                exports = [
                    "Set the environment variable:" if len(keys) == 1 else "Set the environment variables:",
                    _code_block("bash", "\n".join(_export(key, *argument) for key, argument in keys.items())),
                ]
            elements = exports + [CLI_CONFIG_CAPTION, _code_block("yaml", config), REFRESH_WARNING_INCLUDE]
            if test is not None:
                elements += [TEST_CAPTION, _code_block("bash", test.strip("\n"))]
        else:
            elements = [cli.strip("\n")]
        tabs.append(_tab("Holmes CLI", elements))

    for label, release, caption, values_block, upgrade in (
        ("Holmes Helm Chart", "holmes", HOLMES_VALUES_CAPTION, values_text, HOLMES_UPGRADE_COMMAND),
        (
            "Robusta Helm Chart",
            "robusta",
            ROBUSTA_VALUES_CAPTION,
            "holmes:\n" + _indent(values_text, "  "),
            ROBUSTA_UPGRADE_COMMAND,
        ),
    ):
        elements = []
        if deployment_values is not None:
            line = SERVICE_ACCOUNT_LINE + DEPLOYMENT_VALUES[deployment_values]
            elements.append(line.format(release=release))
        if commands:
            elements += [
                SECRET_CAPTION if len(commands) == 1 else SECRETS_CAPTION,
                _code_block("bash", "\n\n".join(commands)),
            ]
        elements += [
            caption,
            _code_block("yaml", values_block),
            APPLY_CAPTION,
            _code_block("bash", upgrade),
        ]
        tabs.append(_tab(label, elements))
    return "\n\n".join(tabs)


def _custom_fences(lines: List[str], page: str, offset: int) -> Iterator[Tuple[int, int, re.Match, str]]:
    """(index of the opening line, index of the closing line, the opening, the body)
    of each fence this module expands in `lines`, which start `offset` lines into the
    page's source; a fence with no closing line fails the build."""
    i = 0
    while i < len(lines):
        opening = SUPPORTED_OPENING_RE.match(lines[i])
        if not opening or opening["region"]:
            i += 1
            continue
        if not page:
            raise TabFenceError(f"a custom fence needs the page's path, {NO_PAGE}")
        end = next((j for j in range(i + 1, len(lines)) if lines[j] == CLOSING_LINE), None)
        if not end:
            raise _unsupported(page, offset + i + 1, lines[i])
        yield i, end, opening, "\n".join(lines[i + 1 : end]).strip("\n")
        i = end + 1


def _unsupported(page: str, line: int, text: str, of: str = "a custom fence") -> TabFenceError:
    return TabFenceError(
        f"{page}:{line}: unsupported form of {of}: "
        f"{text!r}. See the docstring of docs/custom_fences.py "
        "for the supported forms"
    )


def _check_fence_lines(lines: List[str], page: str, offset: int) -> None:
    """Fail the build on a line of `lines`, which start `offset` lines into the page's
    source, that opens or closes a fence in a form no page writes."""
    for i, line in enumerate(lines):
        if FENCE_LINE_RE.match(line) and not (CODE_FENCE_RE.match(line) or SUPPORTED_OPENING_RE.match(line)):
            raise _unsupported(page, offset + i + 1, line, "a fence")


def _code_block_lines(lines: List[str], start: int, end: int) -> List[int]:
    """The lines from `start` to `end` that open, hold or close a code block or a
    robusta-region fence."""
    inside: List[int] = []
    opening = None
    for i in range(start, end):
        if opening is None and (CODE_FENCE_RE.match(lines[i]) or SUPPORTED_OPENING_RE.match(lines[i])):
            opening = i
        elif opening is not None and lines[i].strip() == CLOSING_LINE:
            inside.extend(range(opening, i + 1))
            opening = None
    return inside


def _include_indents(lines: List[str], fences) -> Dict[int, Optional[str]]:
    """The indent an include takes on each line of `lines` that is not unindented:
    CLI_INCLUDE_INDENT in a deployment fence's `cli` field outside its code blocks, and
    None, for no include, on every other line of a fence of `fences`, a code block or a
    robusta-region fence, where pymdownx.snippets would expand it inside the block."""
    indents: Dict[int, Optional[str]] = {}
    previous = 0
    for start, end, opening, _ in fences:
        indents.update(dict.fromkeys(_code_block_lines(lines, previous, start)))
        code = set(_code_block_lines(lines, start + 1, end))
        field = None
        in_fields = False
        for i in range(start + 1, end):
            if opening["deployment"] and not in_fields and lines[i] == FIELDS_SEPARATOR:
                in_fields = True
            elif in_fields and re.match(r"\S", lines[i]):
                field = lines[i].partition(":")[0]
            indents[i] = CLI_INCLUDE_INDENT if field == "cli" and i not in code else None
        previous = end + 1
    indents.update(dict.fromkeys(_code_block_lines(lines, previous, len(lines))))
    return indents


def _check_includes(
    lines: List[str], page: str, offset: int, indents: Optional[Dict[int, Optional[str]]] = None
) -> None:
    """Fail the build on an include in `lines`, which start `offset` lines into the page's
    source, in a form no page writes: not at the indent `indents` gives its line (none,
    for a line it does not name), or of a file that does not exist (pymdownx.snippets
    skips one without an error)."""
    for i, line in enumerate(lines):
        include = SUPPORTED_INCLUDE_RE.match(line)
        if INCLUDE_RE.match(line) and not (
            include
            and include["indent"] == (indents or {}).get(i, "")
            and (SNIPPETS_BASE / include["file"]).is_file()
        ):
            raise _unsupported(page, offset + i + 1, line, "an include")


def _checked(parse, page: str, line: int, text: str):
    """What `parse()` returns for the fence that `text` opens at `line` of the page;
    a FenceBodyError, or None for a body in an unsupported form, fails the build
    naming the fence."""
    try:
        result = parse()
    except FenceBodyError as e:
        raise TabFenceError(f"{page}:{line}: {e}") from e
    if result is None:
        raise _unsupported(page, line, text)
    return result


def source_line_offset(markdown: str, page) -> int:
    """The lines of the page's source above `markdown`: the front matter MkDocs takes
    off before the page hooks and the Markdown pipeline see the page."""
    source = page.file.content_string
    return source[: len(source) - len(markdown)].count("\n")


@dataclass(frozen=True)
class DeploymentFence:
    """A deployment fence of a page, as docs/fence_checks.py checks it."""

    line: int
    # The values the Helm tabs show.
    values: dict
    # The variables the group gives Holmes: its secret's keys and its `additionalEnvVars`.
    environment: Dict[str, str]


def deployment_fences(markdown: str, page: str, offset: int = 0) -> Iterator[DeploymentFence]:
    """Every deployment fence of a page's markdown, which starts `offset` lines into
    the page's source; a fence the preprocessor would refuse fails the build here too."""
    lines = markdown.split("\n")
    for i, _, opening, body in _custom_fences(lines, page, offset):
        if opening["deployment"]:
            line = offset + i + 1
            parsed = _checked(lambda: _deployment_body(opening, body, page), page, line, lines[i])
            yield DeploymentFence(line, _load(parsed.chart_values_text) or {}, parsed.environment)


class TabFencePreprocessor(Preprocessor):
    """Replace each fence with its markdown.

    Registered before pymdownx.snippets, so the lines it counts are the page's own,
    and before superfences and tabbed, which then render the group as they render
    hand-written tabs: tab ids come from tabbed's slugs, as every other tab on the
    page gets them. Snippets then expands the includes of the page and of the
    expansions alike."""

    def __init__(self, md, page: str, offset: int):
        super().__init__(md)
        self.page = page
        self.offset = offset

    def _section(self, opening, body: str):
        if opening["multi"]:
            return _multi_instance_section(body, self.page)
        return _deployment_section(opening, body, self.page)

    def run(self, lines):
        _check_fence_lines(lines, self.page, self.offset)
        fences = list(_custom_fences(lines, self.page, self.offset))
        _check_includes(lines, self.page, self.offset, _include_indents(lines, fences))
        out: list = []
        start = 0
        for i, end, opening, body in fences:
            group = _checked(lambda: self._section(opening, body), self.page, self.offset + i + 1, lines[i])
            out.extend([*lines[start:i], "", *group.split("\n"), ""])
            start = end + 1
        return out + lines[start:]


class TabFencesExtension(Extension):
    def __init__(self, **kwargs):
        self.config = {
            "page": [
                "",
                "Path of the page being converted; its file stem names the page's secrets",
            ],
            "offset": [0, "Lines of the page's source above the markdown converted (its front matter)"],
        }
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        # Before every preprocessor that reads fences or the page's text:
        # pymdownx.snippets (32), so an error names the line in the page's source,
        # pymdownx.critic (31.1), the raw-block stash superfences adds with
        # preserve_tabs (31.05), whitespace normalization (30) and superfences
        # (25), which then see the expansion as hand-written tabs.
        md.preprocessors.register(
            TabFencePreprocessor(md, self.getConfig("page"), self.getConfig("offset")), "tab_fences", 33
        )


def makeExtension(**kwargs):
    return TabFencesExtension(**kwargs)


def on_config(config, **kwargs):
    """MkDocs hook: fail the build on a custom fence in a snippet file, or a fence line or
    an include in a form no page writes. A custom fence is expanded before the includes,
    so only a page's own fences render."""
    docs = Path(config["docs_dir"])
    for path in sorted((docs / "snippets").rglob("*")):
        if path.is_file():
            snippet = path.relative_to(docs).as_posix()
            lines = path.read_text().split("\n")
            for i, line in enumerate(lines):
                if SUPPORTED_OPENING_RE.match(line):
                    raise _unsupported(snippet, i + 1, line)
            _check_fence_lines(lines, snippet, 0)
            _check_includes(lines, snippet, 0, _include_indents(lines, []))
    return config


def on_page_markdown(markdown, page, config, **kwargs):
    """MkDocs hook: give the tab fences the path of the page being built, and where
    its markdown starts in its source.

    It reads two attributes of `page`: `page.file.src_uri`, the path that names the
    page's secrets, the multi-instance link and every error, and
    `page.file.content_string`, the page's source, whose front matter MkDocs has taken
    off `markdown`, so that an error names the line in the source. A caller that runs
    the fences without MkDocs passes a page with both. MkDocs builds each page's
    Markdown instance from `mdx_configs` right after this event."""
    extension = config["mdx_configs"].setdefault(EXTENSION_NAME, {})
    extension["page"] = page.file.src_uri
    extension["offset"] = source_line_offset(markdown, page)
    return markdown


# Text an expansion contains, which a page shows literally when its markdown is
# rendered as text.
EXPANSION_MARKERS = (
    HOLMES_VALUES_CAPTION,
    ROBUSTA_VALUES_CAPTION,
    CLI_CONFIG_CAPTION.partition(". Create")[0],
    MULTI_INSTANCE_LEAD,
)
TAB_MARKDOWN_RE = re.compile(r'(?m)^\s*=== "')


def _text(rendered: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))


def unrendered_expansion(output: str) -> bool:
    """Whether the rendered page shows fence or tab markdown as text: a tab label
    line outside a code block, or a caption of an expansion anywhere."""
    outside_code = re.sub(r"<pre\b.*?</pre>", "", output, flags=re.S)
    return bool(TAB_MARKDOWN_RE.search(_text(outside_code))) or any(
        marker in _text(output) for marker in EXPANSION_MARKERS
    )


def on_post_page(output, page, config, **kwargs):
    """MkDocs hook: fail a page that shows a fence's markdown instead of its tabs.

    The raw lines of a page cannot show the contexts that leave the markdown of
    an expansion on the page as text: raw HTML, and a code block that holds the
    fence."""
    if unrendered_expansion(output):
        raise TabFenceError(
            f"{page.file.src_uri} shows the markdown of a tab fence instead of tabs; "
            "a fence must stand at the start of a line, outside raw HTML and code blocks"
        )
    return output
