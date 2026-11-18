from holmes.plugins.telegram.formatting import (
    markdown_to_telegram_html,
    split_message,
)


def test_bold_headings_lists_and_links():
    html = markdown_to_telegram_html(
        "## Title\n- **item** one\n[docs](https://example.com/x)"
    )
    assert "<b>Title</b>" in html
    assert "• <b>item</b> one" in html
    assert '<a href="https://example.com/x">docs</a>' in html


def test_html_is_escaped_outside_and_inside_code():
    html = markdown_to_telegram_html("a <b> & `x<y` \n```\n<tag>\n```")
    assert "&lt;b&gt; &amp;" in html
    assert "<code>x&lt;y</code>" in html
    assert "<pre>&lt;tag&gt;</pre>" in html


def test_tables_are_monospaced():
    assert markdown_to_telegram_html("| a | b |\n|---|---|\n| 1 | 2 |").startswith(
        "<pre>"
    )


def test_split_message_respects_size_and_keeps_content():
    text = "\n".join(f"line {i}" for i in range(2000))
    parts = split_message(text, size=500)
    assert all(len(p) <= 501 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_split_message_handles_a_single_huge_line():
    parts = split_message("x" * 1200, size=500)
    assert [len(p.strip()) for p in parts] == [500, 500, 200]
    assert "" not in [p for p in parts]
