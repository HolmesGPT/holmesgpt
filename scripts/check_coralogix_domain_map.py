"""Detect drift between CORALOGIX_TEAM_HOSTNAME_SUFFIXES and the official docs.

Coralogix has no API for its region/domain table; the source of truth is the
docs page, which is also served as machine-readable Markdown. This script
fetches that table and compares it with the mapping used for UI permalinks.

Run:  poetry run python scripts/check_coralogix_domain_map.py
Exits non-zero if the docs list a domain we don't map, or map differently.
"""

import re
import sys

import requests  # type: ignore

from holmes.plugins.toolsets.coralogix.utils import CORALOGIX_TEAM_HOSTNAME_SUFFIXES

DOCS_URL = "https://coralogix.com/docs/user-guides/account-management/account-settings/coralogix-domain.md"

# | us2.coralogix.com | US2 | AWS us-west-2 (Oregon) | `<team>.app.cx498.coralogix.com` |
ROW_RE = re.compile(
    r"^\|\s*([a-z0-9.-]+)\s*\|\s*\S+\s*\|[^|]+\|\s*`<team>\.([a-z0-9.-]+)`\s*\|",
    re.MULTILINE,
)


def main() -> int:
    text = requests.get(DOCS_URL, timeout=30).text
    docs_map = dict(ROW_RE.findall(text))
    if not docs_map:
        print(f"ERROR: could not parse any domain rows from {DOCS_URL}")
        return 2

    drift = False
    for domain, suffix in sorted(docs_map.items()):
        mapped = CORALOGIX_TEAM_HOSTNAME_SUFFIXES.get(domain)
        if mapped is None:
            print(f"MISSING : docs list {domain} -> {suffix}, not in map")
            drift = True
        elif mapped != suffix:
            print(f"MISMATCH: {domain} -> map says {mapped}, docs say {suffix}")
            drift = True
        else:
            print(f"OK      : {domain} -> {suffix}")

    # legacy keys aren't in the docs table anymore; list them for visibility
    legacy = sorted(set(CORALOGIX_TEAM_HOSTNAME_SUFFIXES) - set(docs_map))
    for domain in legacy:
        print(
            f"LEGACY  : {domain} -> {CORALOGIX_TEAM_HOSTNAME_SUFFIXES[domain]} (not in docs table; kept for old configs)"
        )

    if drift:
        print(
            "\nDrift detected — update CORALOGIX_TEAM_HOSTNAME_SUFFIXES in holmes/plugins/toolsets/coralogix/utils.py"
        )
        return 1
    print(f"\nMap is in sync with {DOCS_URL} ({len(docs_map)} documented regions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
