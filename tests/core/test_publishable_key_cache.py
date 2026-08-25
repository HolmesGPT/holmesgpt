import time
from unittest.mock import MagicMock, patch

from holmes.core.publishable_key_cache import (
    PUBLISHABLE_KEY_CACHE_TTL_SECONDS,
    PublishableKeyCache,
)


def _response(payload, status_error=None):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.side_effect = status_error
    return response


def test_fetch_key_returns_api_key_and_reports_component():
    cache = PublishableKeyCache()
    with patch("holmes.core.publishable_key_cache.requests.get") as get:
        get.return_value = _response({"api_key": "sb_publishable_x"})
        assert cache.fetch_key("account-1", "cluster-1") == "sb_publishable_x"
    params = get.call_args.kwargs["params"]
    assert params["account_id"] == "account-1"
    assert params["cluster"] == "cluster-1"
    assert params["component"] == "holmes"


def test_fetch_key_returns_none_on_error():
    cache = PublishableKeyCache()
    with patch("holmes.core.publishable_key_cache.requests.get") as get:
        get.return_value = _response({}, status_error=Exception("boom"))
        assert cache.fetch_key("account-1", "cluster-1") is None


def test_fetch_key_returns_none_on_empty_key():
    cache = PublishableKeyCache()
    with patch("holmes.core.publishable_key_cache.requests.get") as get:
        get.return_value = _response({"api_key": ""})
        assert cache.fetch_key("account-1", "cluster-1") is None


def test_store_and_invalidate():
    cache = PublishableKeyCache()
    assert cache.get_cached_key() is None
    cache.store("key-1")
    assert cache.get_cached_key() == "key-1"
    cache.invalidate()
    assert cache.get_cached_key() is None


def test_cached_key_expires():
    cache = PublishableKeyCache()
    cache.store("key-1")
    cache._fetched_at = time.time() - PUBLISHABLE_KEY_CACHE_TTL_SECONDS - 1
    assert cache.get_cached_key() is None
