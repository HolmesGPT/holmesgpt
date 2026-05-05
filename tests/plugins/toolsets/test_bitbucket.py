"""Unit tests for the Bitbucket Cloud toolset."""

import pytest
from pydantic import ValidationError

from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import (
    BitbucketAuthError,
    BitbucketConfig,
    BitbucketForbiddenError,
    BitbucketRateLimitError,
    BitbucketToolset,
)


class TestBitbucketConfig:
    def test_minimum_config(self):
        cfg = BitbucketConfig(api_token="t", workspace="acme")
        assert cfg.api_token == "t"
        assert cfg.workspace == "acme"
        assert cfg.repositories is None
        assert cfg.default_limit == 25
        assert cfg.api_url == "https://api.bitbucket.org/2.0"

    def test_with_repositories_allowlist(self):
        cfg = BitbucketConfig(
            api_token="t",
            workspace="acme",
            repositories=["checkout-api", "inventory-db"],
        )
        assert cfg.repositories == ["checkout-api", "inventory-db"]

    def test_missing_workspace_raises(self):
        with pytest.raises(ValidationError):
            BitbucketConfig(api_token="t")

    def test_api_url_override(self):
        cfg = BitbucketConfig(api_token="t", workspace="acme", api_url="http://localhost:9000")
        assert cfg.api_url == "http://localhost:9000"


class TestBitbucketExceptionsExist:
    """Verify the three custom exception classes are importable RuntimeError subclasses."""

    def test_auth_error(self):
        assert issubclass(BitbucketAuthError, RuntimeError)

    def test_forbidden_error(self):
        assert issubclass(BitbucketForbiddenError, RuntimeError)

    def test_rate_limit_error(self):
        assert issubclass(BitbucketRateLimitError, RuntimeError)
