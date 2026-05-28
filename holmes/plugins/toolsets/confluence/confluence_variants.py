"""Confluence toolset variants that post-process page bodies before returning
them to the LLM.

Three Confluence integrations now exist:

* ``confluence`` (in confluence.py) — returns the raw Confluence REST API
  response untouched. The body of a page is delivered as Confluence storage
  format HTML inside ``body.storage.value``.
* ``confluence_markdown`` (this file) — same auth/endpoints, but every HTML
  string in the response is converted to Markdown before being handed to the
  LLM. Aims to reduce token usage and noise from Confluence storage XML.
* ``confluence_html_filter`` (this file) — same auth/endpoints, but the tool
  exposes a ``css_selector`` parameter. When set, every HTML string in the
  response is filtered through BeautifulSoup ``.select()`` so the LLM only
  sees the matched portion of the page.

The variants reuse all of ``ConfluenceToolset``'s configuration / auth /
gateway / health-check machinery by subclassing it. Each variant overrides
``__init__`` (to register a distinct toolset name) and ``_setup_http_tools``
(to swap in a wrapped HTTP request tool that does the post-processing).
"""

import logging
import re
from typing import Any, Callable, ClassVar, List, Optional, Type

from bs4 import BeautifulSoup
from markdownify import markdownify

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Toolset,
    ToolInvokeContext,
    ToolParameter,
    ToolsetTag,
)
from holmes.plugins.toolsets.confluence.confluence import (
    CONFLUENCE_ICON_URL,
    ConfluenceCloudConfig,
    ConfluenceConfig,
    ConfluenceDataCenterBasicConfig,
    ConfluenceDataCenterPATConfig,
    ConfluenceToolset,
)
from holmes.plugins.toolsets.http.http_toolset import (
    HttpRequest,
    HttpToolset,
    HttpToolsetConfig,
)

logger = logging.getLogger(__name__)

# Cheap heuristic for "this string looks like HTML": at least one tag.
# Used to decide whether to run a string through markdownify / BeautifulSoup.
# Matches `<tag` or `</tag` followed by space, slash, or `>`, including
# Confluence storage-format namespaced macro tags such as `<ac:structured-macro>`
# and `<ri:user>` (so a body that's almost entirely macro markup still gets
# post-processed). False positives on prose containing `<word>` are harmless
# because the converters are a no-op on non-HTML.
_HTML_TAG_RE = re.compile(r"<\s*/?(?:[a-zA-Z][\w-]*:)?[a-zA-Z][\w-]*[\s/>]")


def _looks_like_html(value: str) -> bool:
    return bool(_HTML_TAG_RE.search(value))


def _html_to_markdown(html: str) -> str:
    """Convert a Confluence-storage HTML string to Markdown.

    ``markdownify`` is already a project dependency (used by the internet
    toolset). It tolerates Confluence ``<ac:*>`` / ``<ri:*>`` macro tags by
    treating them as unknown elements and stripping them, which is what we
    want for LLM consumption — the macro markup adds noise but the inner
    text content survives.
    """
    try:
        return markdownify(html, heading_style="ATX").strip()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("markdownify failed, returning raw HTML: %s", e)
        return html


def _filter_html_by_css(html: str, selector: str) -> str:
    """Return the concatenated outer HTML of every element matching ``selector``.

    BeautifulSoup's HTML parser is permissive enough to handle Confluence
    storage XHTML. If the selector matches nothing we return an empty string
    rather than the original HTML — this is the whole point of the filter
    variant: when the agent picks a selector, it should see the consequences.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches = soup.select(selector)
    return "\n".join(str(m) for m in matches)


def _transform_html_strings(value: Any, transform: Callable[[str], str]) -> Any:
    """Walk an arbitrary JSON value and apply ``transform`` to every HTML-ish string."""
    if value is None:
        return None
    if isinstance(value, str):
        return transform(value) if _looks_like_html(value) else value
    if isinstance(value, dict):
        return {k: _transform_html_strings(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [_transform_html_strings(item, transform) for item in value]
    return value


class _MarkdownConvertingHttpRequest(HttpRequest):
    """HttpRequest subclass that converts HTML in successful responses to Markdown."""

    def _invoke(
        self, params: dict, context: ToolInvokeContext
    ) -> StructuredToolResult:
        result = super()._invoke(params, context)
        # Filter (jq/max_depth) has already run by the time we get here, so
        # result.data could be the original {"status_code", "body"} envelope
        # or anything jq returned. Walk it generically.
        if result.status == StructuredToolResultStatus.SUCCESS:
            result.data = _transform_html_strings(result.data, _html_to_markdown)
        return result


class _HtmlFilteringHttpRequest(HttpRequest):
    """HttpRequest subclass that lets the agent supply a CSS selector to filter HTML."""

    def __init__(
        self,
        toolset: HttpToolset,
        tool_name: str = "http_request",
        tool_description: Optional[str] = None,
    ) -> None:
        super().__init__(toolset, tool_name=tool_name, tool_description=tool_description)
        # Add the css_selector parameter on top of the params HttpRequest already
        # registered (url/method/body/headers + JsonFilterMixin's max_depth/jq).
        self.parameters["css_selector"] = ToolParameter(
            description=(
                "Optional CSS selector applied to every HTML string in the "
                "response (typically Confluence's body.storage.value). When "
                "provided, only the matched elements' outer HTML is returned, "
                "which lets you keep the response inside the token budget on "
                "large pages. Selectors follow the BeautifulSoup `.select()` "
                "syntax — e.g. `table`, `h2 + p`, `div.note`, "
                "`section[data-id='runbook'] li`. Omit this parameter to get "
                "the full HTML body."
            ),
            type="string",
            required=False,
        )

    def _invoke(
        self, params: dict, context: ToolInvokeContext
    ) -> StructuredToolResult:
        selector = params.get("css_selector")
        # Strip our extra param before HttpRequest passes the rest through to
        # `requests.request`; otherwise it would get serialized into headers.
        if "css_selector" in params:
            params = {k: v for k, v in params.items() if k != "css_selector"}

        # Validate the selector up front so a malformed value is reported as
        # a recoverable tool error (with the offending selector and parser
        # message) instead of letting BeautifulSoup raise inside the JSON
        # walker. Empty / missing selector skips filtering entirely.
        if selector:
            try:
                BeautifulSoup("", "html.parser").select(selector)
            except Exception as exc:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=(
                        f"Invalid css_selector {selector!r}: {exc}. "
                        "Fix the selector and retry — selectors use "
                        "BeautifulSoup `.select()` syntax."
                    ),
                    params=params,
                )

        result = super()._invoke(params, context)
        if selector and result.status == StructuredToolResultStatus.SUCCESS:
            result.data = _transform_html_strings(
                result.data, lambda html: _filter_html_by_css(html, selector)
            )
        return result


# A tool factory abstracts what each variant injects into the wrapped HttpToolset.
_TOOL_CLASS_MARKDOWN = _MarkdownConvertingHttpRequest
_TOOL_CLASS_HTML_FILTER = _HtmlFilteringHttpRequest


class _ConfluenceVariantBase(ConfluenceToolset):
    """Shared plumbing for the markdown / html-filter Confluence variants.

    Subclasses set:
      * ``_variant_name``    — toolset name registered with Holmes
      * ``_variant_description`` — toolset description shown in the UI
      * ``_tool_class``      — HttpRequest subclass that does the post-processing
      * ``_extra_llm_instructions`` — appended after the base Confluence prompt
        so the LLM knows about variant-specific behavior (markdown output,
        css_selector parameter, etc.)

    Implementation note — the constructor calls ``Toolset.__init__`` directly
    instead of ``super().__init__()``. ``ConfluenceToolset.__init__`` hard-codes
    ``name="confluence"`` and would also re-run the parent's setup, so we skip
    one level up the MRO and pass our own variant-specific name/description.
    All of ``ConfluenceToolset``'s *runtime* behavior (config classes,
    ``prerequisites_callable``, gateway resolution, health checks) is still
    inherited; only the constructor wiring is short-circuited. If
    ``ConfluenceToolset.__init__`` ever grows variant-relevant logic, this
    class must be updated to mirror it.
    """

    _variant_name: ClassVar[str] = ""
    _variant_description: ClassVar[str] = ""
    _tool_class: ClassVar[Type[HttpRequest]] = HttpRequest
    _extra_llm_instructions: ClassVar[str] = ""

    # The variant subtypes mirror the parent class so the same Cloud / DC PAT /
    # DC Basic auth options show up in the UI form for each variant.
    config_classes: ClassVar[List[Type[ConfluenceConfig]]] = [
        ConfluenceCloudConfig,
        ConfluenceDataCenterPATConfig,
        ConfluenceDataCenterBasicConfig,
    ]

    def __init__(self) -> None:
        # Skip ConfluenceToolset.__init__ — we want to call the grandparent
        # Toolset.__init__ directly so the variant gets its own name without
        # duplicating ConfluenceToolset's setup work.
        Toolset.__init__(
            self,
            name=self._variant_name,
            description=self._variant_description,
            icon_url=CONFLUENCE_ICON_URL,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self._gateway_base_url = None

    def _setup_http_tools(self) -> None:
        endpoint = self._build_endpoint_config()
        http_config = HttpToolsetConfig(endpoints=[endpoint])
        # The wrapped HttpToolset's name matters: it's used to derive the LLM
        # tool name (`<name>_request`). Use the variant name so the LLM sees,
        # e.g., `confluence_markdown_request` and `confluence_html_filter_request`.
        http_toolset = HttpToolset(
            name=self._variant_name,
            config=http_config,
            llm_instructions=self._build_variant_llm_instructions(),
            enabled=True,
        )
        ok, msg = http_toolset.prerequisites_callable(http_config.model_dump())
        if not ok:
            raise RuntimeError(f"Failed to initialize HTTP toolset for {self._variant_name}: {msg}")

        # Replace HttpToolset's default HttpRequest with our wrapping subclass.
        # We reuse the tool name/description it already derived.
        original = http_toolset.tools[0]
        wrapped = self._tool_class(
            http_toolset,
            tool_name=original.name,
            tool_description=original.description,
        )
        self.tools = [wrapped]
        self.llm_instructions = http_toolset.llm_instructions

    def _build_variant_llm_instructions(self) -> str:
        base = self._build_llm_instructions()
        if self._extra_llm_instructions:
            return base + "\n\n" + self._extra_llm_instructions
        return base


class ConfluenceMarkdownToolset(_ConfluenceVariantBase):
    """Confluence variant that returns page bodies as Markdown instead of HTML."""

    _variant_name: ClassVar[str] = "confluence_markdown"
    _variant_description: ClassVar[str] = (
        "Fetch and search Confluence pages, returning page bodies converted to Markdown"
    )
    _tool_class: ClassVar[Type[HttpRequest]] = _TOOL_CLASS_MARKDOWN
    _extra_llm_instructions: ClassVar[str] = (
        "### Response post-processing\n\n"
        "This toolset post-processes Confluence responses before you see them: "
        "any HTML string in the JSON body (typically `body.storage.value`, "
        "`body.view.value`, or excerpt fields in CQL search results) is "
        "converted to Markdown. Read the body as Markdown, not as Confluence "
        "storage XHTML. Macros and link metadata are preserved as plain text "
        "where possible; complex Confluence macros may be flattened.\n"
    )


class ConfluenceHtmlFilterToolset(_ConfluenceVariantBase):
    """Confluence variant that lets the agent narrow HTML responses with a CSS selector."""

    _variant_name: ClassVar[str] = "confluence_html_filter"
    _variant_description: ClassVar[str] = (
        "Fetch and search Confluence pages with optional CSS-selector filtering "
        "to keep large page HTML inside the token budget"
    )
    _tool_class: ClassVar[Type[HttpRequest]] = _TOOL_CLASS_HTML_FILTER
    _extra_llm_instructions: ClassVar[str] = (
        "### Response post-processing\n\n"
        "Page bodies are returned as Confluence storage-format HTML, same as "
        "the base `confluence` toolset. In addition, the request tool accepts "
        "an optional `css_selector` parameter. When you supply one, only the "
        "outer HTML of the matched elements is returned for every HTML field "
        "in the response (e.g. `body.storage.value`).\n\n"
        "**Workflow for large pages:**\n\n"
        "1. First call without `css_selector` — but pass `jq` or `max_depth` "
        "to inspect only the page metadata (title, ancestors, version) so you "
        "don't pull the whole body yet.\n"
        "2. Once you know the page's structure, re-fetch with a targeted "
        "`css_selector` such as `h2#runbook + p`, `table.confluenceTable`, "
        "or `[data-macro-name='code']` to retrieve only the relevant section.\n"
        "3. If the selector returns nothing useful, broaden it (`section`, "
        "`div`) or drop it entirely.\n\n"
        "Selectors use BeautifulSoup `.select()` syntax: tag, `.class`, `#id`, "
        "descendant (` `), child (`>`), sibling (`+`), and attribute selectors "
        "all work. Pseudo-classes like `:has()` and `:contains()` are "
        "**not** supported by the underlying parser.\n"
    )


# Re-export so the registration site in toolsets/__init__.py can import from
# the package root (`from holmes.plugins.toolsets.confluence import …`).
__all__ = [
    "ConfluenceHtmlFilterToolset",
    "ConfluenceMarkdownToolset",
]
