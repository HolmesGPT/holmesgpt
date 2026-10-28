"""The gate the docs rebuild job runs before pushing gh-pages: scripts/compare_published_docs.py."""

import gzip
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Dict, Tuple

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "compare_published_docs", REPO / "scripts" / "compare_published_docs.py"
)
assert spec and spec.loader
compare_published_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_published_docs)

FILE = "100644"
SYMLINK = "120000"
Site = Dict[str, Tuple[str, bytes]]


def asset_hash(build: str) -> str:
    return build.replace(".", "") + "0abcdef"


def page(build: str, tab_id: str, title: str, body: str, up: str) -> str:
    return f"""<html><head>
<meta name="generator" content="mkdocs-1.6.1, mkdocs-material-{build}">
<title>{title}</title>
<link rel="stylesheet" href="{up}assets/stylesheets/main.{asset_hash(build)}.min.css">
</head><body>
<nav class="md-nav md-nav--primary"><a href="../">Home</a><a href="./">Guide</a></nav>
<article class="md-content__inner md-typeset">
<input checked="checked" id="__tabbed_{tab_id}_1" name="__tabbed_{tab_id}" type="radio">
<p>{body}</p>
</article>
<script src="{up}assets/javascripts/bundle.{asset_hash(build)}.min.js"></script>
</body></html>"""


def sitemap(lastmod: str, loc: str = "https://example.com/1.0/guide/") -> bytes:
    stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return f"<urlset><url><loc>{loc}</loc>{stamp}</url></urlset>".encode()


def site(
    build: str,
    tab_id: str,
    lastmod: str,
    guide_title: str = "Guide",
    guide_body: str = "Install the agent.",
    guide_search_text: str = "Install the agent.",
    latest_aliases: Tuple[str, ...] = ("latest",),
) -> Site:
    hashed = asset_hash(build)
    files = {
        "versions.json": json.dumps(
            [{"version": "1.0", "title": "1.0", "aliases": list(latest_aliases)}]
        ).encode(),
        "index.html": b'<meta http-equiv="refresh" content="0; url=latest/">',
        "1.0/index.html": page(build, tab_id, "Home", "Welcome.", "").encode(),
        "1.0/guide/index.html": page(
            build, tab_id, guide_title, guide_body, "../"
        ).encode(),
        f"1.0/assets/stylesheets/main.{hashed}.min.css": f"/* material {build} */".encode(),
        f"1.0/assets/javascripts/bundle.{hashed}.min.js": f"/* material {build} */".encode(),
        f"1.0/assets/javascripts/bundle.{hashed}.min.js.map": f'{{"version": "{build}"}}'.encode(),
        "1.0/assets/javascripts/glightbox.min.js": b"/* glightbox */",
        "1.0/search/search_index.json": json.dumps(
            {
                "config": {"lang": ["en"]},
                "docs": [
                    {"location": "", "title": "Home", "text": "Welcome."},
                    {"location": "guide/", "title": "Guide", "text": guide_search_text},
                ],
            }
        ).encode(),
        "1.0/sitemap.xml": sitemap(lastmod),
        "1.0/sitemap.xml.gz": gzip.compress(
            sitemap(lastmod), mtime=int(lastmod.replace("-", ""))
        ),
    }
    return {"latest": (SYMLINK, b"1.0"), **{p: (FILE, c) for p, c in files.items()}}


PUBLISHED = site("9.7.6", "0f3c9a2e_1b2c_4d5e_8f90_a1b2c3d4e5f6", "2026-09-01")
REBUILT_ARGS = ("9.7.7", "7d1e2f3a_4b5c_4d6e_9f01_b2c3d4e5f6a7", "2026-10-16")
REBUILT_SAME_CONTENT = site(*REBUILT_ARGS)


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """A git repo with a tag per tree below, each tag a root commit holding exactly that tree."""
    trees: Dict[str, Site] = {
        "published": PUBLISHED,
        "rebuilt-same-content": REBUILT_SAME_CONTENT,
        "rebuilt-deleted-page": {
            p: f for p, f in REBUILT_SAME_CONTENT.items() if p != "1.0/guide/index.html"
        },
        "rebuilt-new-file": {
            **REBUILT_SAME_CONTENT,
            "1.0/extra/index.html": (FILE, b"<p>Extra.</p>"),
        },
        "rebuilt-latest-not-a-symlink": {
            **REBUILT_SAME_CONTENT,
            "latest": (FILE, b"1.0"),
        },
        "rebuilt-changed-plugin-asset": {
            **REBUILT_SAME_CONTENT,
            "1.0/assets/javascripts/glightbox.min.js": (FILE, b"/* glightbox 2 */"),
        },
        "rebuilt-changed-word": site(*REBUILT_ARGS, guide_body="Install the operator."),
        "rebuilt-changed-title": site(*REBUILT_ARGS, guide_title="Setup"),
        "rebuilt-edited-search-entry": site(
            *REBUILT_ARGS, guide_search_text="Install the operator."
        ),
        "rebuilt-lost-latest-alias": site(*REBUILT_ARGS, latest_aliases=()),
        "rebuilt-missing-linked-theme-file": {
            p: f
            for p, f in REBUILT_SAME_CONTENT.items()
            if not p.startswith("1.0/assets/stylesheets/main.")
        },
        "rebuilt-sitemap-without-lastmod": {
            **REBUILT_SAME_CONTENT,
            "1.0/sitemap.xml": (FILE, sitemap("")),
        },
        "rebuilt-changed-sitemap-entry": {
            **REBUILT_SAME_CONTENT,
            "1.0/sitemap.xml": (
                FILE,
                sitemap(REBUILT_ARGS[2], loc="https://example.com/1.0/setup/"),
            ),
        },
    }
    stream = b""
    for name, files in trees.items():
        stream += f"commit refs/tags/{name}\ncommitter test <test@example.com> 0 +0000\ndata 0\n".encode()
        for rel, (mode, content) in files.items():
            stream += (
                f"M {mode} inline {rel}\ndata {len(content)}\n".encode()
                + content
                + b"\n"
            )
    path = tmp_path_factory.mktemp("gh-pages")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "fast-import", "--quiet"], cwd=path, input=stream, check=True
    )
    return path


def compare(repo: Path, rebuilt: str, monkeypatch) -> int:
    monkeypatch.chdir(repo)
    return compare_published_docs.main(["published", rebuilt])


def test_passes_a_rebuild_that_changes_only_the_theme(repo, monkeypatch, capsys):
    assert compare(repo, "rebuilt-same-content", monkeypatch) == 0
    assert (
        "No content differences between published and rebuilt-same-content (12 files, 7 changed)"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "rebuilt, reported",
    [
        ("rebuilt-deleted-page", "1.0/guide/index.html: missing from the rebuild"),
        ("rebuilt-new-file", "1.0/extra/index.html: new in the rebuild"),
        ("rebuilt-latest-not-a-symlink", "latest: mode 120000 -> 100644"),
        (
            "rebuilt-changed-plugin-asset",
            "1.0/assets/javascripts/glightbox.min.js: differs",
        ),
        (
            "rebuilt-changed-word",
            "1.0/guide/index.html\n    -<p>Install the agent.</p>\n    +<p>Install the operator.</p>",
        ),
        (
            "rebuilt-changed-title",
            "1.0/guide/index.html\n    -<title>Guide</title>\n    +<title>Setup</title>",
        ),
        ("rebuilt-edited-search-entry", "1.0/search/search_index.json: differs"),
        ("rebuilt-lost-latest-alias", "versions.json: differs"),
        ("rebuilt-changed-sitemap-entry", "1.0/sitemap.xml: differs"),
        ("rebuilt-sitemap-without-lastmod", "1.0/sitemap.xml: differs"),
        (
            "rebuilt-missing-linked-theme-file",
            f"1.0/assets/stylesheets/main.{asset_hash(REBUILT_ARGS[0])}.min.css: missing from the rebuild, linked by 2 page(s) such as 1.0/guide/index.html",
        ),
    ],
)
def test_refuses_a_content_difference(repo, monkeypatch, capsys, rebuilt, reported):
    assert compare(repo, rebuilt, monkeypatch) == 1
    out = capsys.readouterr().out
    assert reported in out
    assert out.endswith(f"Content differences between published and {rebuilt}: 1\n")
