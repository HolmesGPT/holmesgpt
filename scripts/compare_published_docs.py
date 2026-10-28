"""Compare the published docs tree with a rebuilt one and exit 1 on any content difference, printing each.

Usage: python scripts/compare_published_docs.py PUBLISHED_REF REBUILT_REF   (run inside the repo, e.g. origin/gh-pages gh-pages)

A rebuild passes when its newer theme release changes only its hashed files and the links to them, so:
  - the theme's hashed files (bundle, main, palette and the search worker, with their source maps) are skipped, but every one a rebuilt page links must exist in the rebuild
  - a page is compared as its whole text, with its generator meta, the hashed theme file names it links and the random tab group ids docs/custom_fences.py generates replaced
  - sitemap.xml is compared with its <lastmod> build dates blanked
  - every other file, search/search_index.json included, is compared byte for byte
"""

import difflib
import gzip
import posixpath
import re
import subprocess
import sys
from typing import Callable, Dict, Iterator, Optional, Tuple

HASHED_THEME_PATH = r"(assets/(?:javascripts/bundle|javascripts/workers/search|stylesheets/main|stylesheets/palette))\.[0-9a-f]{8,}(\.min\.(?:js|css))"
HASHED_THEME_LINK = re.compile(HASHED_THEME_PATH)
HASHED_THEME_FILE = re.compile(rf"^[^/]+/{HASHED_THEME_PATH}(?:\.map)?$")
GENERATOR = re.compile(r'<meta name="generator" content="[^"]*"\s*/?>')
TAB_GROUP_ID = re.compile(
    r"__tabbed_[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}"
)
LASTMOD = re.compile(rb"<lastmod>[^<]*</lastmod>")
BLANK_LASTMOD = b"<lastmod></lastmod>"

Tree = Dict[str, Tuple[str, str]]


def norm(text: str) -> str:
    text = GENERATOR.sub("<meta generator>", text)
    text = HASHED_THEME_LINK.sub(r"\1.HASH\2", text)
    return TAB_GROUP_ID.sub("__tabbed_ID", text)


def tree(ref: str) -> Tree:
    """Map each file path in ref to its (mode, blob id)."""
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", ref], capture_output=True, text=True, check=True
    ).stdout
    entries = {}
    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, _, oid = meta.split()
        entries[path] = (mode, oid)
    return entries


def blob_reader() -> Callable[[str], bytes]:
    batch = subprocess.Popen(
        ["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    requests, replies = batch.stdin, batch.stdout
    assert requests and replies

    def read(oid: str) -> bytes:
        requests.write(oid.encode() + b"\n")
        requests.flush()
        size = int(replies.readline().split()[2])
        data = replies.read(size)
        replies.read(1)
        return data

    return read


def excerpt(old: str, new: str, lines: int = 6) -> str:
    diff = [
        line[:200]
        for line in difflib.unified_diff(
            old.splitlines(), new.splitlines(), n=0, lineterm=""
        )
        if not line.startswith(("---", "+++", "@@"))
    ]
    more = [f"... ({len(diff) - lines} more lines)"] if len(diff) > lines else []
    return "\n    ".join(diff[:lines] + more)


def content_difference(path: str, old: bytes, new: bytes) -> Optional[str]:
    if path.endswith(".html"):
        old_text, new_text = norm(old.decode()), norm(new.decode())
        if old_text == new_text:
            return None
        return f"{path}\n    {excerpt(old_text, new_text)}"
    if path.endswith(("sitemap.xml", "sitemap.xml.gz")):
        if path.endswith(".gz"):
            old, new = gzip.decompress(old), gzip.decompress(new)
        if LASTMOD.sub(BLANK_LASTMOD, old) == LASTMOD.sub(BLANK_LASTMOD, new):
            return None
    return f"{path}: differs"


def differences(
    published: Tree, rebuilt: Tree, read: Callable[[str], bytes]
) -> Iterator[str]:
    """Each content difference, then each hashed theme file a rebuilt page links that the rebuild lacks. A page the rebuild left unchanged counts only for files the published tree had, so a link already broken there is not the rebuild's."""
    linked_by: Dict[str, list] = {}

    def scan_theme_links(path: str, text: str, changed: bool) -> None:
        for link in HASHED_THEME_LINK.finditer(text):
            # The link's "../" prefix is read backwards: a pattern that starts with it has no literal prefix to search for, and scans every page several times slower.
            start = link.start()
            while text.startswith("../", start - 3):
                start -= 3
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), text[start : link.end()])
            )
            if target not in rebuilt and (changed or target in published):
                linked_by.setdefault(target, []).append(path)

    for path in sorted(published.keys() | rebuilt.keys()):
        old, new = published.get(path), rebuilt.get(path)
        new_page = read(new[1]).decode() if new and path.endswith(".html") else None
        if new_page is not None:
            scan_theme_links(path, new_page, old != new)
        if old == new or HASHED_THEME_FILE.match(path):
            continue
        if new is None:
            yield f"{path}: missing from the rebuild"
        elif old is None:
            yield f"{path}: new in the rebuild"
        elif old[0] != new[0]:
            yield f"{path}: mode {old[0]} -> {new[0]}"
        else:
            new_content = new_page.encode() if new_page is not None else read(new[1])
            difference = content_difference(path, read(old[1]), new_content)
            if difference:
                yield difference
    for target, pages in sorted(linked_by.items()):
        yield f"{target}: missing from the rebuild, linked by {len(pages)} page(s) such as {sorted(pages)[0]}"


def main(argv: list) -> int:
    published_ref, rebuilt_ref = argv
    published, rebuilt = tree(published_ref), tree(rebuilt_ref)
    found = 0
    for difference in differences(published, rebuilt, blob_reader()):
        print(difference)
        found += 1
    changed = sum(published.get(path) != entry for path, entry in rebuilt.items())
    if found:
        print(f"Content differences between {published_ref} and {rebuilt_ref}: {found}")
        return 1
    print(
        f"No content differences between {published_ref} and {rebuilt_ref} ({len(rebuilt)} files, {changed} changed)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
