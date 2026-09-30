"""Drift check: CORALOGIX_TEAM_HOSTNAME_SUFFIXES vs the official Coralogix docs.

Coralogix has no API for its region/domain table; the source of truth is the
docs page, which is also served as machine-readable Markdown. The live test
fetches that table and fails if the docs list a domain the map is missing or
maps differently, so the permalink mapping can't silently rot when Coralogix
adds or changes regions. Transient upstream conditions (network errors,
timeouts, 429/5xx) skip rather than fail, so docs-site hiccups don't break
unrelated PRs; drift, a moved page (404), or an unparseable table still fail.
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


def parse_docs_domain_table(markdown_text: str) -> dict[str, str]:
    """Extract {domain: team-hostname-suffix} from the docs Markdown table."""
    return dict(ROW_RE.findall(markdown_text))


def diff_against_map(docs_map: dict[str, str]) -> list[str]:
    """Return one line per documented domain that the map is missing or maps differently."""
    drift = []
    for domain, suffix in sorted(docs_map.items()):
        mapped = CORALOGIX_TEAM_HOSTNAME_SUFFIXES.get(domain)
        if mapped is None:
            drift.append(f"docs list {domain} -> {suffix}, missing from map")
        elif mapped != suffix:
            drift.append(f"{domain}: map says {mapped}, docs say {suffix}")
    return drift


SAMPLE_DOCS_TABLE = """\
| **Coralogix Domain** | **Coralogix Region** | **Region**             | **Team Hostname**                |
| -------------------- | -------------------- | ---------------------- | -------------------------------- |
| us2.coralogix.com    | US2                  | AWS us-west-2 (Oregon) | `<team>.app.cx498.coralogix.com` |
| eu1.coralogix.com    | EU1                  | AWS eu-west-1 (Ireland)| `<team>.coralogix.com`           |
| xx9.coralogix.com    | XX9                  | Nowhere (Fictional)    | `<team>.app.xx9.coralogix.com`   |
"""


def test_docs_table_parsing_and_drift_detection():
    """Hermetic check of the table parser and drift comparison (no network)."""
    docs_map = parse_docs_domain_table(SAMPLE_DOCS_TABLE)
    assert docs_map == {
        "us2.coralogix.com": "app.cx498.coralogix.com",
        "eu1.coralogix.com": "coralogix.com",
        "xx9.coralogix.com": "app.xx9.coralogix.com",
    }
    # us2/eu1 match the real map; the fictional region must be reported as drift
    assert diff_against_map(docs_map) == [
        "docs list xx9.coralogix.com -> app.xx9.coralogix.com, missing from map"
    ]


def test_team_hostname_map_matches_coralogix_docs():
    """Every region in the official docs table must be mapped, with the same hostname."""
    try:
        response = requests.get(DOCS_URL, timeout=30)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        pytest.skip(f"Coralogix docs site unreachable, cannot check drift: {e}")

    if response.status_code == 429 or response.status_code >= 500:
        pytest.skip(
            f"Coralogix docs site returned transient HTTP {response.status_code}, "
            "cannot check drift"
        )

    assert response.status_code == 200, (
        f"Coralogix domain docs page returned HTTP {response.status_code} — "
        f"the page may have moved; update DOCS_URL and verify the mapping: {DOCS_URL}"
    )

    docs_map = parse_docs_domain_table(response.text)
    assert docs_map, (
        f"Could not parse any domain rows from {DOCS_URL} — "
        "the table format may have changed; update ROW_RE and verify the mapping"
    )

    drift = diff_against_map(docs_map)
    assert not drift, (
        "CORALOGIX_TEAM_HOSTNAME_SUFFIXES has drifted from the official docs "
        f"({DOCS_URL}):\n" + "\n".join(drift)
    )
