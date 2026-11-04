import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from holmes.clients import robusta_client as robusta_client_module
from holmes.clients.robusta_client import fetch_supabase_api_key

SAAS_STORE = "https://sp.robusta.dev"
SELF_HOSTED_STORE = "https://robusta-platform-db.example.internal"


@pytest.fixture(autouse=True)
def saas_api_endpoint(monkeypatch):
    monkeypatch.setattr(
        robusta_client_module, "ROBUSTA_API_ENDPOINT", "https://api.robusta.dev"
    )


def _response(payload, error=None):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.side_effect = error
    return response


def test_reports_component_and_returns_key():
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({"api_key": "sb_publishable_x"})
        assert fetch_supabase_api_key("account-1", "cluster-1", SAAS_STORE) == "sb_publishable_x"
    params = get.call_args.kwargs["params"]
    assert params["account_id"] == "account-1"
    assert params["cluster"] == "cluster-1"
    assert params["component"] == "holmes"


def test_returns_none_when_relay_fails():
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({}, error=Exception("relay down"))
        assert fetch_supabase_api_key("account-1", "cluster-1", SAAS_STORE) is None


def test_retries_on_connection_error():
    with patch("holmes.clients.robusta_client.requests.get") as get, patch(
        "holmes.clients.robusta_client.wait_exponential", return_value=lambda *_: 0
    ):
        get.side_effect = [
            requests.exceptions.ConnectionError("boom"),
            _response({"api_key": "sb_publishable_x"}),
        ]
        assert fetch_supabase_api_key("account-1", "cluster-1", SAAS_STORE) == "sb_publishable_x"
    assert get.call_count == 2


@pytest.mark.parametrize(
    "store_url,api_endpoint",
    [
        ("https://sp.robusta.dev", "https://api.robusta.dev"),
        ("https://sp.robusta.dev.", "https://api.robusta.dev"),
        ("https://xyzcompany.supabase.co", "https://api.robusta.dev"),
        # values generated before 2026-05 leave the endpoint on the US default for EU/AP accounts too
        ("https://sp.eu.robusta.dev", "https://api.eu.robusta.dev"),
        ("https://sp.ap.robusta.dev", "https://api.ap.robusta.dev"),
        ("https://sp.eu.robusta.dev.", "https://api.eu.robusta.dev"),
        # tokens issued before the custom domains carry the Supabase project domain
        ("https://xvyhpoxfmtpuqqeyqkso.supabase.co", "https://api.robusta.dev"),
        ("https://fdeazqbagpxzlaumbahw.supabase.co", "https://api.eu.robusta.dev"),
        ("https://xvtsnzetsdaiyobgdlic.supabase.co", "https://api.ap.robusta.dev"),
    ],
)
def test_saas_store_asks_its_regions_api(store_url, api_endpoint):
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({"api_key": "sb_publishable_x"})
        assert (
            fetch_supabase_api_key("account-1", "cluster-1", store_url)
            == "sb_publishable_x"
        )
    assert get.call_args.args[0] == f"{api_endpoint}/api/config/supabase-keys"


@pytest.mark.parametrize(
    "configured_endpoint,store_url,api_endpoint",
    [
        # a store in the map always asks its region's API
        (
            "https://api.eu.robusta.dev",
            "https://sp.ap.robusta.dev",
            "https://api.ap.robusta.dev",
        ),
        (
            "https://stg.api.robusta.dev",
            "https://sp.eu.robusta.dev",
            "https://api.eu.robusta.dev",
        ),
        # any other store asks the configured endpoint
        (
            "https://stg.api.robusta.dev",
            "https://xyzcompany.supabase.co",
            "https://stg.api.robusta.dev",
        ),
        (
            "https://api.eu.robusta.dev",
            "https://xyzcompany.supabase.co",
            "https://api.eu.robusta.dev",
        ),
    ],
)
def test_configured_endpoint_is_used_for_stores_outside_the_map(
    monkeypatch, configured_endpoint, store_url, api_endpoint
):
    monkeypatch.setattr(
        robusta_client_module, "ROBUSTA_API_ENDPOINT", configured_endpoint
    )
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({"api_key": "sb_publishable_x"})
        fetch_supabase_api_key("account-1", "cluster-1", store_url)
    assert get.call_args.args[0] == f"{api_endpoint}/api/config/supabase-keys"


@pytest.mark.parametrize(
    "store_url",
    [
        SELF_HOSTED_STORE,
        "http://kong.robusta-platform.svc.cluster.local:8000",
        "https://robusta.dev.example.com",
    ],
)
def test_does_not_reach_saas_for_a_self_hosted_store(store_url):
    """ROB-1743: a self-hosted Holmes left on the SaaS endpoint must not call out of the cluster."""
    with patch("holmes.clients.robusta_client.requests.get") as get:
        assert fetch_supabase_api_key("account-1", "cluster-1", store_url) is None
    get.assert_not_called()


def test_malformed_endpoint_falls_back_instead_of_raising(monkeypatch):
    monkeypatch.setattr(
        robusta_client_module, "ROBUSTA_API_ENDPOINT", "https://[api.robusta.dev"
    )
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.side_effect = requests.exceptions.InvalidURL("bad url")
        assert (
            fetch_supabase_api_key("account-1", "cluster-1", SELF_HOSTED_STORE) is None
        )


def test_fetches_from_a_self_hosted_endpoint(monkeypatch):
    monkeypatch.setattr(
        robusta_client_module,
        "ROBUSTA_API_ENDPOINT",
        "http://relay.robusta-platform.svc",
    )
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({"api_key": "self-hosted-key"})
        assert (
            fetch_supabase_api_key("account-1", "cluster-1", SELF_HOSTED_STORE)
            == "self-hosted-key"
        )
    assert (
        get.call_args.args[0]
        == "http://relay.robusta-platform.svc/api/config/supabase-keys"
    )


def test_skip_log_shows_only_the_store_host(caplog):
    with caplog.at_level(logging.INFO), patch(
        "holmes.clients.robusta_client.requests.get"
    ):
        assert (
            fetch_supabase_api_key(
                "account-1", "cluster-1", "https://user:secret@db.example.internal/x?k=v"
            )
            is None
        )
    assert "db.example.internal" in caplog.text
    assert "secret" not in caplog.text and "k=v" not in caplog.text
