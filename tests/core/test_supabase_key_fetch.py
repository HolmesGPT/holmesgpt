from unittest.mock import MagicMock, patch

from holmes.clients.robusta_client import fetch_supabase_api_key


def _response(payload, error=None):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.side_effect = error
    return response


def test_reports_component_and_returns_key():
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({"api_key": "sb_publishable_x"})
        assert fetch_supabase_api_key("account-1", "cluster-1") == "sb_publishable_x"
    params = get.call_args.kwargs["params"]
    assert params["account_id"] == "account-1"
    assert params["cluster"] == "cluster-1"
    assert params["component"] == "holmes"


def test_returns_none_when_relay_fails():
    with patch("holmes.clients.robusta_client.requests.get") as get:
        get.return_value = _response({}, error=Exception("relay down"))
        assert fetch_supabase_api_key("account-1", "cluster-1") is None


def test_retries_on_connection_error():
    import requests

    with patch("holmes.clients.robusta_client.requests.get") as get, patch(
        "holmes.clients.robusta_client.wait_exponential", return_value=lambda *_: 0
    ):
        get.side_effect = [
            requests.exceptions.ConnectionError("boom"),
            _response({"api_key": "sb_publishable_x"}),
        ]
        assert fetch_supabase_api_key("account-1", "cluster-1") == "sb_publishable_x"
    assert get.call_count == 2
