"""Render tools are offered unless the renderer probe shows there is no renderer."""

import pytest
import requests
import responses

from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaToolset

GRAFANA_URL = "http://grafana.test:3000"
VERSION_URL = f"{GRAFANA_URL}/api/rendering/version"
PROBE_URL = f"{GRAFANA_URL}/render/d-solo/nonexistent/_"


def _render_tool_names(rsps: responses.RequestsMock, enable_rendering: bool = True) -> set:
    rsps.add(responses.GET, f"{GRAFANA_URL}/api/dashboards/tags", json=[])
    toolset = GrafanaToolset()
    ok, msg = toolset.prerequisites_callable(
        {"api_url": GRAFANA_URL, "enable_rendering": enable_rendering}
    )
    assert ok, msg
    return {t.name for t in toolset.tools} & {"grafana_render_panel", "grafana_render_dashboard"}


@pytest.mark.parametrize(
    "probe_response,expected",
    [
        ({"status": 200}, True),
        ({"status": 500}, True),
        ({"body": requests.exceptions.ReadTimeout("Read timed out")}, True),
        ({"status": 404}, False),
        ({"body": requests.exceptions.ConnectionError("refused")}, False),
        ({"body": requests.exceptions.ConnectTimeout("connect timed out")}, False),
    ],
    ids=[
        "probe-200",
        "probe-500",
        "probe-read-timeout",
        "probe-404",
        "probe-connection-error",
        "probe-connect-timeout",
    ],
)
def test_render_probe(probe_response, expected):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, VERSION_URL, status=404)
        rsps.add(responses.GET, PROBE_URL, **probe_response)
        tools = _render_tool_names(rsps)
    assert bool(tools) is expected
    if expected:
        assert tools == {"grafana_render_panel", "grafana_render_dashboard"}


def test_version_api_skips_the_render_probe():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, VERSION_URL, json={"version": "3.12.0"})
        tools = _render_tool_names(rsps)
        assert all("/render/" not in call.request.url for call in rsps.calls)
    assert tools == {"grafana_render_panel", "grafana_render_dashboard"}


def test_rendering_disabled_never_probes():
    with responses.RequestsMock() as rsps:
        assert _render_tool_names(rsps, enable_rendering=False) == set()
        assert all("render" not in call.request.url for call in rsps.calls)
