"""Unit tests for SupabaseDal."""

import logging
from unittest.mock import Mock, patch

import pytest
from postgrest.exceptions import APIError as PGAPIError

from holmes.core.supabase_dal import (
    FIREWALL_TROUBLESHOOTING_URL,
    GROUPED_ISSUES_TABLE,
    ISSUES_TABLE,
    SupabaseConnectionException,
    SupabaseDal,
    SupabaseDnsException,
)


class TestSignIn:
    """Tests for SupabaseDal.sign_in() error classification.

    A firewall / egress policy that blocks the cluster from reaching the Robusta
    platform surfaces as a connection reset/refused during sign-in. Holmes should
    convert that into a SupabaseConnectionException whose message points the user
    at their firewall, instead of leaking a raw httpx traceback. Genuine auth
    errors must still propagate unchanged.
    """

    @pytest.fixture
    def mock_dal(self):
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.client = Mock()
            dal.url = "https://sp.eu.robusta.dev"
            dal.email = "user@example.com"
            dal.password = "secret"
            return dal

    def test_connection_reset_raises_firewall_exception(self, mock_dal, caplog):
        # The exact error Aviva hit at startup (ROB-273): httpx surfaces the
        # firewall block as "[Errno 104] Connection reset by peer".
        mock_dal.client.auth.sign_in_with_password.side_effect = Exception(
            "[Errno 104] Connection reset by peer"
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(SupabaseConnectionException) as exc_info:
                mock_dal.sign_in()

        # The exception stays a thin technical wrapper - it names the platform and
        # the underlying error but carries none of the actionable guidance.
        message = str(exc_info.value)
        assert "Robusta platform" in message
        assert "curl" not in message
        assert "*.robusta.dev" not in message
        assert FIREWALL_TROUBLESHOOTING_URL not in message

        # All the firewall guidance - cause, the allowlist fix, and the docs link -
        # is logged at WARNING (not ERROR, so it doesn't raise a Sentry alert)
        # before the exception is raised.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("firewall" in r.getMessage().lower() for r in warnings)
        assert any("*.robusta.dev" in r.getMessage() for r in warnings)
        assert any(FIREWALL_TROUBLESHOOTING_URL in r.getMessage() for r in warnings)

    def test_connection_refused_raises_firewall_exception(self, mock_dal):
        mock_dal.client.auth.sign_in_with_password.side_effect = (
            ConnectionRefusedError("[Errno 111] Connection refused")
        )
        with pytest.raises(SupabaseConnectionException):
            mock_dal.sign_in()

    def test_timeout_raises_firewall_exception(self, mock_dal):
        mock_dal.client.auth.sign_in_with_password.side_effect = TimeoutError(
            "connection timed out"
        )
        with pytest.raises(SupabaseConnectionException):
            mock_dal.sign_in()

    def test_dns_error_still_raises_dns_exception(self, mock_dal):
        mock_dal.client.auth.sign_in_with_password.side_effect = Exception(
            "Temporary failure in name resolution"
        )
        with pytest.raises(SupabaseDnsException):
            mock_dal.sign_in()

    def test_auth_error_is_not_wrapped(self, mock_dal):
        # A genuine credential error is not a connectivity/firewall problem;
        # wrapping it would mislead the user, so it must propagate unchanged.
        original = ValueError("Invalid login credentials")
        mock_dal.client.auth.sign_in_with_password.side_effect = original
        with pytest.raises(ValueError) as exc_info:
            mock_dal.sign_in()
        assert exc_info.value is original

    def test_successful_sign_in_returns_user_id(self, mock_dal):
        session = Mock(access_token="access-token", refresh_token="refresh-token")
        res = Mock(session=session, user=Mock(id="user-123"))
        mock_dal.client.auth.sign_in_with_password.return_value = res

        assert mock_dal.sign_in() == "user-123"
        mock_dal.client.auth.set_session.assert_called_once_with(
            "access-token", "refresh-token"
        )
        mock_dal.client.postgrest.auth.assert_called_once_with("access-token")


class TestIsRealtimeEnabled:
    """Tests for SupabaseDal.is_realtime_enabled()."""

    @pytest.fixture
    def mock_dal(self):
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"
            dal.client = Mock()
            return dal

    def _set_rpc_result(self, mock_dal, *, data=None, raise_exc=None):
        rpc_chain = Mock()
        if raise_exc is not None:
            rpc_chain.execute.side_effect = raise_exc
        else:
            res = Mock()
            res.data = data
            rpc_chain.execute.return_value = res
        mock_dal.client.rpc.return_value = rpc_chain
        return rpc_chain

    def test_returns_true_when_rpc_returns_true(self, mock_dal):
        self._set_rpc_result(mock_dal, data=True)
        assert mock_dal.is_realtime_enabled() is True
        mock_dal.client.rpc.assert_called_once_with("is_realtime_enabled", {})

    def test_returns_false_when_rpc_returns_false(self, mock_dal):
        self._set_rpc_result(mock_dal, data=False)
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_false_when_rpc_returns_list_of_false(self, mock_dal):
        # Some PostgREST responses wrap scalar return values in a single-row list.
        self._set_rpc_result(mock_dal, data=[False])
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_true_when_rpc_returns_list_of_true(self, mock_dal):
        self._set_rpc_result(mock_dal, data=[True])
        assert mock_dal.is_realtime_enabled() is True

    def test_returns_false_when_rpc_does_not_exist_pgrst202(self, mock_dal):
        exc = PGAPIError(
            {"code": "PGRST202", "message": "Could not find the function"}
        )
        self._set_rpc_result(mock_dal, raise_exc=exc)
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_false_when_rpc_does_not_exist_message_match(self, mock_dal):
        exc = PGAPIError(
            {
                "code": "OTHER",
                "message": "Could not find the function public.is_realtime_enabled",
            }
        )
        self._set_rpc_result(mock_dal, raise_exc=exc)
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_none_on_other_api_error(self, mock_dal):
        exc = PGAPIError({"code": "PGRST301", "message": "JWT expired"})
        self._set_rpc_result(mock_dal, raise_exc=exc)
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_on_connectivity_error(self, mock_dal):
        self._set_rpc_result(mock_dal, raise_exc=ConnectionError("network down"))
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_when_dal_disabled(self, mock_dal):
        mock_dal.enabled = False
        assert mock_dal.is_realtime_enabled() is None
        mock_dal.client.rpc.assert_not_called()

    def test_returns_none_on_empty_list_response(self, mock_dal):
        # An empty list from PostgREST means no rows — there's no value to
        # coerce, so we should treat it as inconclusive rather than
        # collapsing to False.
        self._set_rpc_result(mock_dal, data=[])
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_on_null_data(self, mock_dal):
        # Likewise, an explicit None payload is inconclusive — not a
        # definitive False.
        self._set_rpc_result(mock_dal, data=None)
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_true_for_dict_with_enabled_true(self, mock_dal):
        # A SQL function variant could return a row instead of a scalar.
        self._set_rpc_result(mock_dal, data={"enabled": True})
        assert mock_dal.is_realtime_enabled() is True

    def test_returns_false_for_dict_with_enabled_false(self, mock_dal):
        # And the same row shape with the field set to false. Naive
        # bool(data) would have wrongly returned True here.
        self._set_rpc_result(mock_dal, data={"enabled": False})
        assert mock_dal.is_realtime_enabled() is False

    def test_returns_true_for_dict_with_enabled_truthy_in_list(self, mock_dal):
        self._set_rpc_result(mock_dal, data=[{"enabled": True}])
        assert mock_dal.is_realtime_enabled() is True

    def test_returns_none_for_dict_without_enabled_key(self, mock_dal):
        # Unknown dict shape — refuse to guess.
        self._set_rpc_result(mock_dal, data={"other": True})
        assert mock_dal.is_realtime_enabled() is None

    def test_returns_none_for_unexpected_payload_type(self, mock_dal):
        # A string (or any other unexpected type) is inconclusive — we
        # won't fall back to truthy/falsy coercion.
        self._set_rpc_result(mock_dal, data="true")
        assert mock_dal.is_realtime_enabled() is None


class TestGetIssueDataFiring:
    """Tests that get_issue_data exposes a uniform `firing` boolean.

    The firing state is what tells Holmes whether an alert/issue is currently
    active or already resolved. For prometheus alerts it comes from the explicit
    `firing` column on GroupedIssues; for every other source it is derived from
    `ends_at` (null => still firing).
    """

    @pytest.fixture
    def mock_dal(self):
        with patch("holmes.core.supabase_dal.create_client"):
            dal = SupabaseDal(cluster="test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"
            dal.client = Mock()
            return dal

    def _setup_tables(self, mock_dal, issue_row, grouped_row=None):
        """Wire client.table() so the Issues/GroupedIssues/Evidence lookups in
        get_issue_data resolve to the supplied rows (Evidence is left empty)."""

        def make_single_row_chain(row):
            chain = Mock()
            chain.select.return_value = chain
            chain.filter.return_value = chain
            res = Mock()
            res.data = [row] if row is not None else []
            chain.execute.return_value = res
            return chain

        # Evidence query: select().eq().not_.in_().execute() -> empty data
        evidence_chain = Mock()
        evidence_chain.select.return_value = evidence_chain
        evidence_chain.eq.return_value = evidence_chain
        evidence_chain.in_.return_value = evidence_chain
        evidence_chain.not_ = evidence_chain
        evidence_res = Mock()
        evidence_res.data = []
        evidence_chain.execute.return_value = evidence_res

        issue_chain = make_single_row_chain(issue_row)
        grouped_chain = make_single_row_chain(grouped_row)

        def table_side_effect(table_name):
            if table_name == ISSUES_TABLE:
                return issue_chain
            if table_name == GROUPED_ISSUES_TABLE:
                return grouped_chain
            return evidence_chain

        mock_dal.client.table.side_effect = table_side_effect

    def test_non_prometheus_firing_when_ends_at_is_none(self, mock_dal):
        self._setup_tables(
            mock_dal,
            issue_row={"id": "abc", "source": "kubernetes", "ends_at": None},
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        assert data["firing"] is True

    def test_non_prometheus_resolved_when_ends_at_is_set(self, mock_dal):
        self._setup_tables(
            mock_dal,
            issue_row={
                "id": "abc",
                "source": "kubernetes",
                "ends_at": "2026-06-07T10:00:00Z",
            },
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        assert data["firing"] is False

    def test_prometheus_uses_explicit_grouped_issues_firing_flag(self, mock_dal):
        # The Issues row points at prometheus, so get_issue_data re-fetches the
        # GroupedIssues row, which carries the explicit firing flag. A resolved
        # alert keeps firing=False even though we don't recompute it.
        self._setup_tables(
            mock_dal,
            issue_row={"id": "abc", "source": "prometheus", "ends_at": None},
            grouped_row={
                "id": "abc",
                "source": "prometheus",
                "firing": False,
                "ends_at": "2026-06-07T10:00:00Z",
            },
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        # Explicit flag from GroupedIssues is preserved, not overwritten.
        assert data["firing"] is False

    def test_prometheus_firing_flag_true_is_preserved(self, mock_dal):
        self._setup_tables(
            mock_dal,
            issue_row={"id": "abc", "source": "prometheus", "ends_at": None},
            grouped_row={
                "id": "abc",
                "source": "prometheus",
                "firing": True,
                "ends_at": None,
            },
        )
        data = mock_dal.get_issue_data("abc")
        assert data is not None
        assert data["firing"] is True
