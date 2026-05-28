"""Unit tests for the confluence_markdown / confluence_html_filter variants.

The variants share auth/config plumbing with the base ConfluenceToolset (covered
by test_confluence_tools.py); these tests focus on the behavior unique to the
variants — HTML→Markdown conversion and CSS-selector filtering of responses.
"""

from unittest.mock import patch

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.confluence.confluence_variants import (
    ConfluenceHtmlFilterToolset,
    ConfluenceMarkdownToolset,
    _filter_html_by_css,
    _html_to_markdown,
    _looks_like_html,
    _transform_html_strings,
)
from holmes.plugins.toolsets.http.http_toolset import HttpRequest, HttpToolset


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_looks_like_html_detects_tags(self):
        assert _looks_like_html("<p>hi</p>")
        assert _looks_like_html("text with <strong>bold</strong>")
        assert _looks_like_html("<br/>")

    def test_looks_like_html_rejects_plain_text(self):
        assert not _looks_like_html("hello world")
        assert not _looks_like_html("just numbers 1 2 3")
        assert not _looks_like_html("")

    def test_looks_like_html_detects_confluence_macro_tags(self):
        # Confluence storage XHTML is full of <ac:*>/<ri:*> macro tags.
        # The detector must trigger on those even when a body has no plain
        # HTML tags, otherwise variants leave macro-only bodies untouched.
        assert _looks_like_html(
            "<ac:structured-macro ac:name='code'></ac:structured-macro>"
        )
        assert _looks_like_html("<ri:user ri:account-id='abc'/>")

    def test_html_to_markdown_basic(self):
        md = _html_to_markdown("<p>Code: <strong>XYZ</strong></p>")
        assert "**XYZ**" in md
        assert "Code:" in md

    def test_html_to_markdown_strips_confluence_macros(self):
        # Confluence storage XHTML includes <ac:*>/<ri:*> macro tags.
        # markdownify treats unknown tags as transparent so the inner text
        # survives, which is what we want for the LLM.
        html = (
            "<p>before</p>"
            "<ac:structured-macro ac:name='code'>"
            "<ac:plain-text-body><![CDATA[print('hi')]]></ac:plain-text-body>"
            "</ac:structured-macro>"
            "<p>after</p>"
        )
        md = _html_to_markdown(html)
        assert "before" in md
        assert "after" in md

    def test_filter_html_by_css_returns_matched_outer_html(self):
        html = "<div><p>one</p><p class='target'>two</p><p>three</p></div>"
        assert _filter_html_by_css(html, "p.target") == '<p class="target">two</p>'

    def test_filter_html_by_css_returns_empty_string_on_no_match(self):
        # Empty string is intentional — when the agent picks a bad selector
        # it should see "no match" rather than the unfiltered body.
        assert _filter_html_by_css("<p>hi</p>", "table.audit") == ""


# ---------------------------------------------------------------------------
# JSON walker
# ---------------------------------------------------------------------------


class TestTransformHtmlStrings:
    def test_walks_nested_dicts_and_lists(self):
        data = {
            "results": [
                {"body": {"storage": {"value": "<p>page one</p>"}}},
                {"body": {"storage": {"value": "<p>page two</p>"}}},
            ],
            "title": "plain title",  # not HTML — should be untouched
        }
        out = _transform_html_strings(data, _html_to_markdown)
        assert out["title"] == "plain title"
        assert "page one" in out["results"][0]["body"]["storage"]["value"]
        assert "<p>" not in out["results"][0]["body"]["storage"]["value"]

    def test_leaves_non_string_scalars_alone(self):
        data = {"id": 12345, "active": True, "rev": None, "tags": []}
        assert _transform_html_strings(data, _html_to_markdown) == data


# ---------------------------------------------------------------------------
# Variant toolset wiring
# ---------------------------------------------------------------------------


def _stub_setup(cls):
    """Run prerequisites_callable past the live HTTP/health checks."""
    config = {
        "api_url": "https://example.atlassian.net",
        "user": "u@x.com",
        "api_key": "k",
    }
    ts = cls()
    with (
        patch.object(cls, "_perform_health_check", return_value=(True, "ok")),
        patch.object(HttpToolset, "_check_endpoint_health", return_value=(True, "")),
    ):
        ok, _ = ts.prerequisites_callable(config)
    assert ok, f"{cls.__name__} prerequisites_callable returned False"
    return ts


class TestVariantWiring:
    def test_markdown_variant_registers_correct_tool(self):
        ts = _stub_setup(ConfluenceMarkdownToolset)
        assert ts.name == "confluence_markdown"
        assert len(ts.tools) == 1
        tool = ts.tools[0]
        assert tool.name == "confluence_markdown_request"
        # Must be the wrapping subclass, not the bare HttpRequest.
        from holmes.plugins.toolsets.confluence.confluence_variants import (
            _MarkdownConvertingHttpRequest,
        )

        assert isinstance(tool, _MarkdownConvertingHttpRequest)
        # The variant-specific instructions are appended.
        assert "Markdown" in (ts.llm_instructions or "")

    def test_html_filter_variant_registers_css_selector_param(self):
        ts = _stub_setup(ConfluenceHtmlFilterToolset)
        assert ts.name == "confluence_html_filter"
        tool = ts.tools[0]
        assert tool.name == "confluence_html_filter_request"
        assert "css_selector" in tool.parameters
        assert tool.parameters["css_selector"].required is False
        from holmes.plugins.toolsets.confluence.confluence_variants import (
            _HtmlFilteringHttpRequest,
        )

        assert isinstance(tool, _HtmlFilteringHttpRequest)
        assert "css_selector" in (ts.llm_instructions or "")


# ---------------------------------------------------------------------------
# Response post-processing (end-to-end through the wrapping tool)
# ---------------------------------------------------------------------------


def _success_result(body):
    return StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS,
        data={"status_code": 200, "body": body},
    )


class TestMarkdownVariantPostProcessing:
    def test_html_in_response_is_converted(self):
        ts = _stub_setup(ConfluenceMarkdownToolset)
        tool = ts.tools[0]
        confluence_page = {
            "id": "1",
            "title": "Runbook",
            "body": {
                "storage": {
                    "value": "<h1>Header</h1><p>Code: <strong>HOLMES-MD</strong></p>",
                    "representation": "storage",
                }
            },
        }
        with patch.object(
            HttpRequest, "_invoke", return_value=_success_result(confluence_page)
        ):
            result = tool._invoke({"url": "https://example.atlassian.net/x"}, context=None)

        body_value = result.data["body"]["body"]["storage"]["value"]
        assert "<h1>" not in body_value
        assert "**HOLMES-MD**" in body_value
        assert "# Header" in body_value

    def test_non_html_strings_are_left_alone(self):
        ts = _stub_setup(ConfluenceMarkdownToolset)
        tool = ts.tools[0]
        with patch.object(
            HttpRequest,
            "_invoke",
            return_value=_success_result({"id": "1", "title": "Plain title"}),
        ):
            result = tool._invoke({"url": "https://example.atlassian.net/x"}, context=None)
        assert result.data["body"]["title"] == "Plain title"

    def test_error_responses_are_passed_through_untouched(self):
        ts = _stub_setup(ConfluenceMarkdownToolset)
        tool = ts.tools[0]
        err = StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error="HTTP 404",
        )
        with patch.object(HttpRequest, "_invoke", return_value=err):
            result = tool._invoke({"url": "https://example.atlassian.net/x"}, context=None)
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.error == "HTTP 404"


class TestHtmlFilterVariantPostProcessing:
    def test_css_selector_narrows_the_body(self):
        ts = _stub_setup(ConfluenceHtmlFilterToolset)
        tool = ts.tools[0]
        page = {
            "id": "1",
            "body": {
                "storage": {
                    "value": (
                        "<h1>Audit Page</h1>"
                        "<p>Distractor paragraph.</p>"
                        "<table class='audit'><tr><td>code-A</td></tr></table>"
                    ),
                    "representation": "storage",
                }
            },
        }
        with patch.object(HttpRequest, "_invoke", return_value=_success_result(page)):
            result = tool._invoke(
                {
                    "url": "https://example.atlassian.net/x",
                    "css_selector": "table.audit",
                },
                context=None,
            )
        body_value = result.data["body"]["body"]["storage"]["value"]
        assert "code-A" in body_value
        assert "Distractor" not in body_value
        assert "<h1>" not in body_value

    def test_css_selector_is_stripped_before_request(self):
        # If css_selector leaked through to the underlying HttpRequest it
        # would be ignored at best, or surfaced as a header at worst. Verify
        # it is not present in the params dict the parent _invoke sees.
        ts = _stub_setup(ConfluenceHtmlFilterToolset)
        tool = ts.tools[0]
        seen_params = {}

        def fake_invoke(self_inner, params, context):
            seen_params.update(params)
            return _success_result({"body": {"storage": {"value": "<p>x</p>"}}})

        with patch.object(HttpRequest, "_invoke", new=fake_invoke):
            tool._invoke(
                {
                    "url": "https://example.atlassian.net/x",
                    "css_selector": "p",
                },
                context=None,
            )
        assert "css_selector" not in seen_params
        assert seen_params["url"] == "https://example.atlassian.net/x"

    def test_invalid_css_selector_returns_recoverable_error(self):
        ts = _stub_setup(ConfluenceHtmlFilterToolset)
        tool = ts.tools[0]
        # Patch HttpRequest._invoke to fail loudly if reached — a malformed
        # selector must short-circuit before any HTTP work.
        with patch.object(
            HttpRequest,
            "_invoke",
            side_effect=AssertionError("HTTP request must not be made on bad selector"),
        ):
            result = tool._invoke(
                {
                    "url": "https://example.atlassian.net/x",
                    "css_selector": "::::nope",
                },
                context=None,
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "::::nope" in result.error
        assert "css_selector" in result.error.lower() or "selector" in result.error.lower()

    def test_omitting_css_selector_returns_full_body(self):
        ts = _stub_setup(ConfluenceHtmlFilterToolset)
        tool = ts.tools[0]
        html = "<h1>Audit</h1><p>full content</p>"
        with patch.object(
            HttpRequest,
            "_invoke",
            return_value=_success_result(
                {"body": {"storage": {"value": html, "representation": "storage"}}}
            ),
        ):
            result = tool._invoke({"url": "https://example.atlassian.net/x"}, context=None)
        body_value = result.data["body"]["body"]["storage"]["value"]
        assert body_value == html
