"""Drift check: CORALOGIX_TEAM_HOSTNAME_SUFFIXES vs the official Coralogix docs.

Coralogix has no API for its region/domain table; the source of truth is the
docs page, which is also served as machine-readable Markdown. This test fetches
that table and fails if the docs list a domain the map is missing or maps
differently, so the permalink mapping can't silently rot when Coralogix adds
or changes regions.
"""

import re

import pytest
import requests  # type: ignore

from holmes.plugins.toolsets.coralogix.utils import CORALOGIX_TEAM_HOSTNAME_SUFFIXES

DOCS_URL = "https://coralogix.com/docs/user-guides/account-management/account-settings/coralogix-domain.md"

# | us2.coralogix.com | US2 | AWS us-west-2 (Oregon) | `<team>.app.cx498.coralogix.com` |
ROW_RE = re.compile(
    r"^\|\s*([a-z0-9.-]+)\s*\|\s*\S+\s*\|[^|]+\|\s*`<team>\.([a-z0-9.-]+)`\s*\|",
    re.MULTILINE,
)


def test_team_hostname_map_matches_coralogix_docs():
    """Every region in the official docs table must be mapped, with the same hostname."""
    try:
        response = requests.get(DOCS_URL, timeout=30)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        pytest.skip(f"Coralogix docs site unreachable, cannot check drift: {e}")

    assert response.status_code == 200, (
        f"Coralogix domain docs page returned HTTP {response.status_code} — "
        f"the page may have moved; update DOCS_URL and verify the mapping: {DOCS_URL}"
    )

    docs_map = dict(ROW_RE.findall(response.text))
    assert docs_map, (
        f"Could not parse any domain rows from {DOCS_URL} — "
        "the table format may have changed; update ROW_RE and verify the mapping"
    )

    drift = []
    for domain, suffix in sorted(docs_map.items()):
        mapped = CORALOGIX_TEAM_HOSTNAME_SUFFIXES.get(domain)
        if mapped is None:
            drift.append(f"docs list {domain} -> {suffix}, missing from map")
        elif mapped != suffix:
            drift.append(f"{domain}: map says {mapped}, docs say {suffix}")

    assert not drift, (
        "CORALOGIX_TEAM_HOSTNAME_SUFFIXES has drifted from the official docs "
        f"({DOCS_URL}):\n" + "\n".join(drift)
    )
