"""Helpers to turn LLM Markdown into messages that Telegram accepts."""

import re

TELEGRAM_MESSAGE_LIMIT = 4096
# Chunks are split below the hard limit: the HTML conversion adds tags, which count towards it.
CHUNK_SIZE = 3500


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def markdown_to_telegram_html(text: str) -> str:
    """Convert LLM Markdown to the HTML subset supported by Telegram (parse_mode=HTML)."""
    stash: list[str] = []

    def keep(html: str) -> str:
        stash.append(html)
        return f"\x00{len(stash) - 1}\x00"

    # Fenced code blocks and tables (runs of lines starting with "|") are shown monospaced.
    text = re.sub(
        r"```[^\n`]*\n(.*?)```",
        lambda m: keep(f"<pre>{escape_html(m.group(1).rstrip())}</pre>"),
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"(?m)((?:^[ \t]*\|.*\n?)+)",
        lambda m: keep(f"<pre>{escape_html(m.group(1).rstrip())}</pre>\n"),
        text,
    )
    text = re.sub(
        r"`([^`\n]+)`", lambda m: keep(f"<code>{escape_html(m.group(1))}</code>"), text
    )
    text = escape_html(text)
    text = re.sub(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*$", r"<b>\1</b>", text)
    text = re.sub(
        r"\*\*(.+?)\*\*|__(.+?)__",
        lambda m: f"<b>{m.group(1) or m.group(2)}</b>",
        text,
    )
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"(?m)^([ \t]*)[-*][ \t]+", lambda m: m.group(1) + "• ", text)
    text = re.sub(r"(?m)^[ \t]*([-*_])\1{2,}[ \t]*$", "", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


def split_message(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split on line boundaries so every chunk stays below ``size`` characters."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > size:
            out.append(line[:size])
            line = line[size:]
        if current and len(current) + len(line) + 1 > size:
            out.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        out.append(current)
    return out
