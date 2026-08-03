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


@pytest.fixture
def skills_dal():
    """A SupabaseDal with a stubbed client, shared by the skill-related test classes."""
    with patch("holmes.core.supabase_dal.create_client"):
        dal = SupabaseDal(cluster="test-cluster")
        dal.enabled = True
        dal.client = Mock()
        dal.account_id = "acct-1"
        # Holmes's own service identity. Personal reads must never fall back to it.
        dal.user_id = "holmes-service-user"
        return dal


class TestPersonalSkills:
    """Tests for the personal-skill DAL reads.

    These cover the seam the skill_loader tests cannot: those mock the DAL entirely, so
    they never exercise the RPC call shape or the response parsing. The failure mode here
    is silent -- a wrong parameter name or an unparsed response yields an empty personal
    tier with no error, and Holmes simply never loads anyone's personal skills.
    """


    @staticmethod
    def _row(**overrides):
        row = {
            "runbook_id": "uuid-1",
            "subject_name": "My skill",
            "symptoms": "when the thing breaks",
            "runbook": {"instructions": ["step one"]},
            "alerts": [],
            "clusters": None,
            "enabled": True,
        }
        row.update(overrides)
        return row

    # ── get_personal_skill_catalog ──

    def test_calls_security_definer_rpc_with_end_user_id(self, skills_dal):
        """Must go through the RPC, not a table select.

        Personal rows are owner-only under RLS and Holmes authenticates as a service user,
        so a plain select would match zero rows.
        """
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[self._row()]
        )

        result = skills_dal.get_personal_skill_catalog("end-user-1")

        assert result is not None and len(result) == 1
        fn_name, params = skills_dal.client.rpc.call_args[0]
        assert fn_name == "get_personal_skills"
        # Parameter names must match the SQL function signature exactly
        assert params == {"_account_id": "acct-1", "_user_id": "end-user-1"}
        # The table must not be read directly
        skills_dal.client.table.assert_not_called()

    def test_parses_rows_into_instructions(self, skills_dal):
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[self._row(runbook_id="uuid-9", subject_name="Disk full")]
        )

        result = skills_dal.get_personal_skill_catalog("end-user-1")

        assert result[0].id == "uuid-9"
        assert result[0].title == "Disk full"
        assert result[0].symptom == "when the thing breaks"

    def test_skips_disabled_skills(self, skills_dal):
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[self._row(enabled=False)]
        )

        assert skills_dal.get_personal_skill_catalog("end-user-1") == []

    def test_skips_skills_without_symptom(self, skills_dal):
        """A skill with no symptom cannot be matched, so it is dropped."""
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[self._row(symptoms=None)]
        )

        assert skills_dal.get_personal_skill_catalog("end-user-1") == []

    def test_filters_by_cluster(self, skills_dal):
        """Cluster scoping happens here, before any hierarchy dedup upstream."""
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[
                self._row(runbook_id="here", clusters=["test-cluster"]),
                self._row(runbook_id="elsewhere", clusters=["other-cluster"]),
                self._row(runbook_id="all-clusters", clusters=None),
            ]
        )

        result = skills_dal.get_personal_skill_catalog("end-user-1")

        assert {r.id for r in result} == {"here", "all-clusters"}

    def test_no_user_id_does_not_call_rpc(self, skills_dal):
        """The server-initiated guardrail, enforced at the DAL too."""
        assert skills_dal.get_personal_skill_catalog("") is None
        assert skills_dal.get_personal_skill_catalog(None) is None
        skills_dal.client.rpc.assert_not_called()

    def test_returns_none_when_disabled(self, skills_dal):
        skills_dal.enabled = False
        assert skills_dal.get_personal_skill_catalog("end-user-1") is None
        skills_dal.client.rpc.assert_not_called()

    def test_rpc_error_returns_none_rather_than_raising(self, skills_dal):
        """A failed read must not break the whole chat request."""
        skills_dal.client.rpc.side_effect = PGAPIError({"message": "boom"})

        assert skills_dal.get_personal_skill_catalog("end-user-1") is None

    def test_missing_instructions_yields_empty_string_not_none(self, skills_dal):
        """Must be "" rather than str(None).

        Callers fall back with `instruction or pretty()`, and the string "None" is truthy --
        it would suppress the fallback and hand the LLM the literal text "None" as the
        skill body.
        """
        assert skills_dal._extract_skill_instruction({"runbook": {}}, "x") == ""
        assert skills_dal._extract_skill_instruction({}, "x") == ""
        assert (
            skills_dal._extract_skill_instruction(
                {"runbook": {"instructions": None}}, "x"
            )
            == ""
        )

    def test_empty_instruction_list_yields_empty_string_not_bracket_literal(
        self, skills_dal
    ):
        """`instructions: []` must be "" for the same reason None must not become "None".

        str([]) == "[]" is truthy, so returning it would suppress the caller's
        `instruction or pretty()` fallback and hand the LLM "[]" as the skill body.
        """
        assert (
            skills_dal._extract_skill_instruction({"runbook": {"instructions": []}}, "x")
            == ""
        )

    @pytest.mark.parametrize("shape", [[], ["a"], "str", 7])
    def test_non_dict_runbook_yields_empty_string_rather_than_raising(
        self, skills_dal, shape
    ):
        """`runbook` is jsonb with no shape constraint, so it can hold a list or scalar.

        `.get` on those raises AttributeError, which the caller converts into a silently
        dropped skill.
        """
        assert skills_dal._extract_skill_instruction({"runbook": shape}, "x") == ""

    def test_unexpected_instruction_type_is_not_logged_verbatim(
        self, skills_dal, caplog
    ):
        """Personal skill bodies are private and must never reach shared logs."""
        secret = {"private": "SECRET-BODY-DO-NOT-LOG"}

        skills_dal._extract_skill_instruction({"runbook": {"instructions": secret}}, "x")

        assert "SECRET-BODY-DO-NOT-LOG" not in caplog.text
        assert "dict" in caplog.text

    def test_one_malformed_row_does_not_drop_the_whole_tier(self, skills_dal):
        """A row missing a required field must cost only that row.

        id and title are required on the model, so an invalid row raises. If that reached
        the outer handler the user would silently lose every personal skill.
        """
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[
                self._row(runbook_id=None),          # invalid: id is required
                self._row(runbook_id="ok-1"),
                self._row(subject_name=None),        # invalid: title is required
                self._row(runbook_id="ok-2"),
            ]
        )

        result = skills_dal.get_personal_skill_catalog("end-user-1")

        assert {r.id for r in result} == {"ok-1", "ok-2"}

    # ── get_personal_skill_content ──

    def test_content_rpc_is_scoped_to_the_user(self, skills_dal):
        """user_id is part of the lookup so one user cannot fetch another's body."""
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[self._row()]
        )

        result = skills_dal.get_personal_skill_content("uuid-1", "end-user-1")

        assert result is not None
        fn_name, params = skills_dal.client.rpc.call_args[0]
        assert fn_name == "get_personal_skill_content"
        assert params == {
            "_account_id": "acct-1",
            "_user_id": "end-user-1",
            "_runbook_id": "uuid-1",
        }

    def test_content_normalizes_instructions_list(self, skills_dal):
        skills_dal.client.rpc.return_value.execute.return_value = Mock(
            data=[self._row(runbook={"instructions": ["only step"]})]
        )

        result = skills_dal.get_personal_skill_content("uuid-1", "end-user-1")

        assert result.instruction == "only step"

    def test_content_missing_returns_none(self, skills_dal):
        skills_dal.client.rpc.return_value.execute.return_value = Mock(data=[])

        assert skills_dal.get_personal_skill_content("nope", "end-user-1") is None

    def test_content_without_user_id_does_not_call_rpc(self, skills_dal):
        assert skills_dal.get_personal_skill_content("uuid-1", None) is None
        skills_dal.client.rpc.assert_not_called()


class TestSkillHierarchyConfig:
    """Tests for reading the per-account name-collision policy from AccountSettings.

    Every failure path must fall back to disabled, because silently enabling collision
    resolution would start dropping skills that used to run.
    """


    def _settings(self, skills_dal, settings):
        chain = skills_dal.client.table.return_value.select.return_value.eq.return_value
        chain.execute.return_value = Mock(data=[{"settings": settings}])

    def test_defaults_to_disabled_when_unset(self, skills_dal):
        self._settings(skills_dal, {})

        config = skills_dal.get_skill_hierarchy_config()

        assert config.enabled is False
        assert config.order == ["global", "custom", "personal"]

    @pytest.mark.parametrize("raw", ["false", "true", 0, 1, "", "no", None])
    def test_non_boolean_enabled_is_treated_as_false(self, skills_dal, raw):
        """bool("false") is True.

        This jsonb is written by hand-run SQL, so the JSON *string* "false" is a realistic
        mistake -- and coercing it would silently turn the hierarchy ON and start
        suppressing skills that used to run.
        """
        self._settings(skills_dal, {"skill_name_hierarchy_enabled": raw})

        assert skills_dal.get_skill_hierarchy_config().enabled is False

    def test_reads_enabled_and_order(self, skills_dal):
        self._settings(
            skills_dal,
            {
                "skill_name_hierarchy_enabled": True,
                "skill_name_hierarchy_order": ["personal", "custom", "global"],
            },
        )

        config = skills_dal.get_skill_hierarchy_config()

        assert config.enabled is True
        assert config.order == ["personal", "custom", "global"]

    def test_reads_from_account_settings_table(self, skills_dal):
        self._settings(skills_dal, {})

        skills_dal.get_skill_hierarchy_config()

        skills_dal.client.table.assert_called_with("AccountSettings")

    def test_malformed_order_falls_back_to_default(self, skills_dal):
        self._settings(
            skills_dal,
            {"skill_name_hierarchy_enabled": True, "skill_name_hierarchy_order": "nonsense"},
        )

        config = skills_dal.get_skill_hierarchy_config()

        assert config.order == ["global", "custom", "personal"]

    def test_no_row_falls_back_to_disabled(self, skills_dal):
        chain = skills_dal.client.table.return_value.select.return_value.eq.return_value
        chain.execute.return_value = Mock(data=[])

        assert skills_dal.get_skill_hierarchy_config().enabled is False

    def test_read_failure_falls_back_to_disabled(self, skills_dal):
        skills_dal.client.table.side_effect = PGAPIError({"message": "boom"})

        assert skills_dal.get_skill_hierarchy_config().enabled is False

    def test_result_is_cached_across_calls(self, skills_dal):
        """This is read on every chat request, so it must not hit Supabase every turn."""
        self._settings(skills_dal, {"skill_name_hierarchy_enabled": True})

        first = skills_dal.get_skill_hierarchy_config()
        second = skills_dal.get_skill_hierarchy_config()

        assert first.enabled is True and second.enabled is True
        assert skills_dal.client.table.call_count == 1

    def test_failure_is_cached_too(self, skills_dal):
        """A persistent read failure must not retry Supabase on every request."""
        skills_dal.client.table.side_effect = PGAPIError({"message": "boom"})

        skills_dal.get_skill_hierarchy_config()
        skills_dal.get_skill_hierarchy_config()

        assert skills_dal.client.table.call_count == 1


class TestSyncSkills:
    """Tests for the HolmesCustomSkills mirror write."""


    def test_upserts_and_prunes_stale_names(self, skills_dal):
        rows = [
            {"account_id": "acct-1", "cluster_id": "c1", "skill_name": "alpha"},
            {"account_id": "acct-1", "cluster_id": "c1", "skill_name": "beta"},
        ]

        skills_dal.sync_skills(rows, "c1")

        table = skills_dal.client.table
        table.assert_called_with("HolmesCustomSkills")
        upsert = table.return_value.upsert
        upsert.assert_called_once_with(
            rows, on_conflict="account_id, cluster_id, skill_name"
        )
        # stale rows for this (account, cluster) are removed
        not_in = table.return_value.delete.return_value.eq.return_value.eq.return_value.not_.in_
        not_in.assert_called_once_with("skill_name", ["alpha", "beta"])

    def test_empty_list_does_not_delete_everything(self, skills_dal):
        """"No skills loaded" is indistinguishable from a load failure, so an empty list
        must not wipe the UI's view."""
        skills_dal.sync_skills([], "c1")

        skills_dal.client.table.assert_not_called()

    def test_disabled_dal_is_a_noop(self, skills_dal):
        skills_dal.enabled = False

        skills_dal.sync_skills(
            [{"account_id": "acct-1", "cluster_id": "c1", "skill_name": "alpha"}], "c1"
        )

        skills_dal.client.table.assert_not_called()

    def test_write_failure_is_swallowed(self, skills_dal):
        """A display-only mirror must never break startup."""
        skills_dal.client.table.side_effect = PGAPIError({"message": "boom"})

        skills_dal.sync_skills(
            [{"account_id": "acct-1", "cluster_id": "c1", "skill_name": "alpha"}], "c1"
        )

class TestGlobalSkillCatalog:
    """Regression tests for the global (account-wide) skill catalog read.

    Same shape as the personal read, so it had the same defect: one malformed row aborted
    the loop and the account silently lost every global skill.
    """

    @staticmethod
    def _row(**overrides):
        row = {
            "runbook_id": "uuid-1",
            "subject_name": "Global skill",
            "symptoms": "when the thing breaks",
            "clusters": None,
            "enabled": True,
        }
        row.update(overrides)
        return row

    def _rows(self, skills_dal, rows):
        chain = (
            skills_dal.client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value
        )
        chain.execute.return_value = Mock(data=rows)

    def test_one_malformed_row_does_not_drop_the_whole_catalog(self, skills_dal):
        self._rows(
            skills_dal,
            [
                self._row(runbook_id=None),      # invalid: id is required
                self._row(runbook_id="ok-1"),
                self._row(subject_name=None),    # invalid: title is required
                self._row(runbook_id="ok-2"),
            ],
        )

        result = skills_dal.get_skill_catalog()

        assert {r.id for r in result} == {"ok-1", "ok-2"}

    def test_filters_by_cluster_and_symptom(self, skills_dal):
        self._rows(
            skills_dal,
            [
                self._row(runbook_id="here", clusters=["test-cluster"]),
                self._row(runbook_id="elsewhere", clusters=["other-cluster"]),
                self._row(runbook_id="no-symptom", symptoms=None),
                self._row(runbook_id="all-clusters", clusters=None),
            ],
        )

        result = skills_dal.get_skill_catalog()

        assert {r.id for r in result} == {"here", "all-clusters"}
