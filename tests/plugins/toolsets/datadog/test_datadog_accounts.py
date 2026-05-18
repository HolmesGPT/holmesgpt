"""Tests for the multi-account Datadog config plumbing.

Covers the backward-compatible single-account shorthand, the new
``accounts:`` list form, and the ``get_account()`` resolver used by every
Datadog tool to route a call to the right account.
"""

import pytest

from holmes.plugins.toolsets.datadog.datadog_api import (
    DatadogAccount,
    DatadogBaseConfig,
)


def _base_config(**kwargs):
    return DatadogBaseConfig(**kwargs)


class TestLegacyShorthand:
    """The single-account shorthand must keep working unchanged."""

    def test_shorthand_synthesizes_default_account(self):
        cfg = _base_config(
            api_key="k", app_key="a", api_url="https://api.datadoghq.com"
        )
        assert len(cfg.accounts) == 1
        account = cfg.accounts[0]
        assert account.name == "default"
        assert account.default is True
        assert account.api_key == "k"
        assert account.app_key == "a"
        assert str(account.api_url).rstrip("/") == "https://api.datadoghq.com"

    def test_shorthand_missing_field_raises(self):
        with pytest.raises(ValueError, match="missing required field"):
            _base_config(api_key="k", api_url="https://api.datadoghq.com")

    def test_no_credentials_raises(self):
        with pytest.raises(ValueError, match="missing credentials"):
            _base_config()


class TestAccountsList:
    """The new ``accounts:`` form supports N accounts."""

    def test_accounts_list_preserves_names(self):
        cfg = _base_config(
            accounts=[
                DatadogAccount(
                    name="staging",
                    api_key="ks",
                    app_key="as",
                    api_url="https://api.datadoghq.eu",
                ),
                DatadogAccount(
                    name="production",
                    api_key="kp",
                    app_key="ap",
                    api_url="https://api.datadoghq.eu",
                    default=True,
                ),
            ]
        )
        assert [a.name for a in cfg.accounts] == ["staging", "production"]
        assert cfg.accounts[1].default is True

    def test_first_account_becomes_default_when_unset(self):
        cfg = _base_config(
            accounts=[
                DatadogAccount(
                    name="a", api_key="k", app_key="a", api_url="https://x.example"
                ),
                DatadogAccount(
                    name="b", api_key="k", app_key="a", api_url="https://x.example"
                ),
            ]
        )
        assert cfg.accounts[0].default is True
        assert cfg.accounts[1].default is False

    def test_both_forms_together_is_rejected(self):
        with pytest.raises(ValueError, match="either the top-level"):
            _base_config(
                api_key="k",
                app_key="a",
                api_url="https://api.datadoghq.com",
                accounts=[
                    DatadogAccount(
                        name="x",
                        api_key="k",
                        app_key="a",
                        api_url="https://api.datadoghq.com",
                    )
                ],
            )

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValueError, match="duplicate account name"):
            _base_config(
                accounts=[
                    DatadogAccount(
                        name="dup",
                        api_key="k",
                        app_key="a",
                        api_url="https://x.example",
                    ),
                    DatadogAccount(
                        name="dup",
                        api_key="k",
                        app_key="a",
                        api_url="https://x.example",
                    ),
                ]
            )

    def test_multiple_defaults_rejected(self):
        with pytest.raises(ValueError, match="at most one account"):
            _base_config(
                accounts=[
                    DatadogAccount(
                        name="a",
                        api_key="k",
                        app_key="a",
                        api_url="https://x.example",
                        default=True,
                    ),
                    DatadogAccount(
                        name="b",
                        api_key="k",
                        app_key="a",
                        api_url="https://x.example",
                        default=True,
                    ),
                ]
            )


class TestGetAccount:
    """`get_account` is what every tool calls to resolve the target account."""

    @pytest.fixture
    def cfg(self):
        return _base_config(
            accounts=[
                DatadogAccount(
                    name="staging",
                    api_key="ks",
                    app_key="as",
                    api_url="https://api.datadoghq.eu",
                ),
                DatadogAccount(
                    name="production",
                    api_key="kp",
                    app_key="ap",
                    api_url="https://api.datadoghq.eu",
                    default=True,
                ),
            ]
        )

    def test_none_returns_default(self, cfg):
        assert cfg.get_account(None).name == "production"

    def test_by_name(self, cfg):
        assert cfg.get_account("staging").name == "staging"

    def test_unknown_lists_available(self, cfg):
        with pytest.raises(KeyError) as exc:
            cfg.get_account("nope")
        msg = str(exc.value)
        assert "'staging'" in msg
        assert "'production'" in msg
        assert "'nope'" in msg
