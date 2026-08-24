"""Normalization and, more importantly, the redaction rules the schema promises."""

import pytest

from holmes.core.investigation_path.normalize import (
    classify_error,
    event_from_tool_call,
    looks_like_instance_id,
    normalize_resource_kind,
    normalize_resource_name,
    path_from_tool_calls,
)
from holmes.core.investigation_path.schema import (
    ErrorClass,
    OutcomeStatus,
    QueryIntent,
    SignatureLevel,
)
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


def tool_call(
    tool_name,
    params,
    status=StructuredToolResultStatus.SUCCESS,
    data="raw tool output that must never be stored",
    error=None,
    call_id="call-1",
):
    return ToolCallResult(
        tool_call_id=call_id,
        tool_name=tool_name,
        description=tool_name,
        result=StructuredToolResult(status=status, data=data, error=error, params=params),
    )


class TestNameNormalization:
    @pytest.mark.parametrize(
        "token,expected",
        [("x2k9p", True), ("7d9f8b6c5", True), ("0", True), ("cache", False), ("redis", False)],
    )
    def test_looks_like_instance_id(self, token, expected):
        assert looks_like_instance_id(token) is expected

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("payment-service-7d9f8b6c5-x2k9p", "payment-service"),
            ("redis-0", "redis"),
            ("redis-cache", "redis-cache"),
            ("  Redis  ", "redis"),
            ("", ""),
        ],
    )
    def test_normalize_resource_name(self, name, expected):
        assert normalize_resource_name(name) == expected

    def test_two_pods_of_one_deployment_normalize_together(self):
        assert normalize_resource_name("payment-service-7d9f8b6c5-x2k9p") == normalize_resource_name(
            "payment-service-5f4d2a1b7-qq8lm"
        )

    @pytest.mark.parametrize(
        "kind,expected", [("po", "pod"), ("svc", "service"), ("Pod", "pod"), ("widget", "widget")]
    )
    def test_normalize_resource_kind(self, kind, expected):
        assert normalize_resource_kind(kind) == expected


class TestRedaction:
    def test_tool_output_is_never_stored(self):
        event = event_from_tool_call(
            tool_call("kubectl_get", {"kind": "svc", "name": "redis"}, data="SECRET PAYLOAD"),
            ordinal=0,
        )
        assert "SECRET PAYLOAD" not in event.model_dump_json()

    def test_raw_error_text_is_replaced_by_a_class(self):
        event = event_from_tool_call(
            tool_call(
                "kubectl_get",
                {"kind": "svc", "name": "redis"},
                status=StructuredToolResultStatus.ERROR,
                error='Error from server (Forbidden): user "svc-acct-xyz" cannot list services',
            ),
            ordinal=0,
        )
        assert event.outcome.status == OutcomeStatus.ERROR
        assert event.outcome.error_class == ErrorClass.FORBIDDEN
        assert "svc-acct-xyz" not in event.model_dump_json()

    @pytest.mark.parametrize(
        "params",
        [
            {"name": "redis", "password": "hunter2"},
            {"name": "redis", "api_key": "sk-abc123"},
            {"name": "redis", "authorization": "Bearer abc.def.ghi"},
            {"name": "redis", "session_token": "zzz"},
        ],
    )
    def test_sensitive_parameters_are_never_read(self, params):
        event = event_from_tool_call(tool_call("kubectl_get", params), ordinal=0)
        dumped = event.model_dump_json()
        for value in params.values():
            if value != "redis":
                assert value not in dumped

    def test_unknown_parameters_are_ignored_entirely(self):
        event = event_from_tool_call(
            tool_call("kubectl_get", {"kind": "svc", "name": "redis", "vendor_blob": "leak-me"}),
            ordinal=0,
        )
        assert "leak-me" not in event.model_dump_json()

    def test_high_entropy_words_in_commands_are_redacted(self):
        event = event_from_tool_call(
            tool_call("run_bash_command", {"command": "curl h7sk39dnq82mfk20xbz1qq host"}),
            ordinal=0,
        )
        assert "h7sk39dnq82mfk20xbz1qq" not in event.model_dump_json()

    def test_evidence_is_a_reference_not_a_copy(self):
        event = event_from_tool_call(
            tool_call("kubectl_get", {"kind": "svc", "name": "redis"}, call_id="call-42"),
            ordinal=0,
        )
        assert event.evidence_ref == "call-42"


class TestIntentInference:
    @pytest.mark.parametrize(
        "command,intent",
        [
            ("kubectl describe pod payment-service-7d9f8b6c5-x2k9p", QueryIntent.DESCRIBE),
            ("kubectl logs payment-service-7d9f8b6c5-x2k9p", QueryIntent.LOGS),
            ("kubectl get endpoints redis", QueryIntent.TOPOLOGY),
            ("kubectl get svc redis", QueryIntent.TOPOLOGY),
            ("kubectl get pods", QueryIntent.LIST),
            ("kubectl top pods", QueryIntent.RESOURCE_USAGE),
            ("kubectl rollout history deployment/api", QueryIntent.CONFIG_HISTORY),
        ],
    )
    def test_intent_comes_from_the_command_verb(self, command, intent):
        event = event_from_tool_call(tool_call("run_bash_command", {"command": command}), ordinal=0)
        assert event.intent == intent

    @pytest.mark.parametrize(
        "tool_name,intent",
        [
            ("fetch_pod_logs", QueryIntent.LOGS),
            ("prometheus_query", QueryIntent.METRICS),
            ("fetch_traces", QueryIntent.TRACES),
            ("kubectl_events", QueryIntent.EVENTS),
        ],
    )
    def test_intent_falls_back_to_the_tool_name(self, tool_name, intent):
        event = event_from_tool_call(tool_call(tool_name, {"name": "api"}), ordinal=0)
        assert event.intent == intent

    def test_same_check_through_two_toolsets_shares_a_signature(self):
        from holmes.core.investigation_path.schema import signature_of

        via_bash = event_from_tool_call(
            tool_call("run_bash_command", {"command": "kubectl logs api-7d9f8b6c5-x2k9p"}), 0
        )
        via_toolset = event_from_tool_call(
            tool_call("fetch_pod_logs", {"kind": "pod", "name": "api-5f4d2a1b7-qq8lm"}), 1
        )
        assert signature_of(via_bash) == signature_of(via_toolset)


class TestCommandTargets:
    """Reading the resource out of a shell command.

    A command that names a resource must produce the same entity however the
    resource was written, or the benchmark reports a check as skipped after it
    was executed - a false positive caused entirely by parsing.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "kubectl rollout history deployment/catalog-service",
            "kubectl rollout history deployment catalog-service",
            "kubectl rollout history deploy/catalog-service",
            "kubectl rollout status deployment/catalog-service",
        ],
    )
    def test_a_rollout_target_is_read_past_the_sub_verb(self, command):
        """`history` sits where the kind normally goes; it is not the kind."""
        event = event_from_tool_call(tool_call("run_bash_command", {"command": command}), 0)
        assert event.entity.kind == "deployment"
        assert event.entity.name == "catalog-service"

    def test_a_rollout_check_matches_the_corpus_reference_step(self):
        """INC-012 stores `config_history:deployment:<subject>`. Parsing the
        command to `config_history:history:deployment/catalog-service` made the
        benchmark score an executed check as missing."""
        from holmes.core.investigation_path.schema import signature_of

        event = event_from_tool_call(
            tool_call(
                "run_bash_command",
                {"command": "kubectl rollout history deployment/catalog-service"},
            ),
            0,
        )
        assert (
            signature_of(event, subject="catalog-service")
            == "config_history:deployment:<subject>"
        )

    @pytest.mark.parametrize(
        "slashed, spaced",
        [
            ("kubectl describe deployment/checkout-api", "kubectl describe deployment checkout-api"),
            ("kubectl get pod/checkout-api", "kubectl get pod checkout-api"),
            ("kubectl get svc/redis", "kubectl get service redis"),
        ],
    )
    def test_the_slash_form_and_the_spaced_form_are_one_check(self, slashed, spaced):
        from holmes.core.investigation_path.schema import signature_of

        a = event_from_tool_call(tool_call("run_bash_command", {"command": slashed}), 0)
        b = event_from_tool_call(tool_call("run_bash_command", {"command": spaced}), 1)
        assert signature_of(a) == signature_of(b)

    def test_an_unknown_kind_before_a_slash_is_not_treated_as_a_target(self):
        """Otherwise a path argument would be parsed as a resource."""
        from holmes.core.investigation_path.normalize import split_resource_target

        assert split_resource_target("./manifests/app.yaml") == (None, None)
        assert split_resource_target("notakind/thing") == (None, None)
        assert split_resource_target("plainname") == (None, None)
        assert split_resource_target("deployment/") == (None, None)

    def test_a_bare_name_after_logs_is_still_a_pod(self):
        event = event_from_tool_call(
            tool_call("run_bash_command", {"command": "kubectl logs checkout-api-7d9f8b6c5-x2k9p"}),
            0,
        )
        assert event.entity.kind == "pod"
        assert event.entity.name == "checkout-api"

    def test_a_kind_with_no_name_keeps_the_kind(self):
        event = event_from_tool_call(
            tool_call("run_bash_command", {"command": "kubectl get pods"}), 0
        )
        assert event.entity.kind == "pod"
        assert event.entity.name is None


class TestEntityAndTimeWindow:
    def test_kind_and_name_are_combined(self):
        event = event_from_tool_call(tool_call("kubectl_get", {"kind": "svc", "name": "redis"}), 0)
        assert event.entity.kind == "service"
        assert event.entity.name == "redis"

    def test_metric_is_read_out_of_a_promql_query(self):
        event = event_from_tool_call(
            tool_call("prometheus_query", {"query": 'sum(rate(redis_connected_clients{a="b"}[5m]))'}),
            0,
        )
        assert event.entity.kind == "metric"
        assert event.entity.name == "redis_connected_clients"

    def test_lookback_is_bucketed_so_near_identical_ranges_match(self):
        seven = event_from_tool_call(tool_call("fetch_pod_logs", {"name": "api", "since": "7m"}), 0)
        ten = event_from_tool_call(tool_call("fetch_pod_logs", {"name": "api", "since": "10m"}), 1)
        assert seven.time_window.lookback_seconds == ten.time_window.lookback_seconds == 900

    def test_state_reads_are_marked_point_in_time(self):
        event = event_from_tool_call(tool_call("kubectl_get", {"kind": "svc", "name": "redis"}), 0)
        assert event.time_window.is_point_in_time is True


class TestPathConstruction:
    def test_ordering_is_preserved(self):
        path = path_from_tool_calls(
            [
                tool_call("kubectl_describe", {"kind": "pod", "name": "api"}, call_id="a"),
                tool_call("fetch_pod_logs", {"kind": "pod", "name": "api"}, call_id="b"),
                tool_call("kubectl_get", {"kind": "svc", "name": "redis"}, call_id="c"),
            ]
        )
        assert [event.ordinal for event in path.events] == [0, 1, 2]
        assert path.signatures() == [
            "describe:pod:api",
            "logs:pod:api",
            "topology:service:redis",
        ]

    def test_repeated_checks_collapse_but_keep_first_position(self):
        path = path_from_tool_calls(
            [
                tool_call("fetch_pod_logs", {"kind": "pod", "name": "api-7d9f8b6c5-x2k9p"}, call_id="a"),
                tool_call("kubectl_get", {"kind": "svc", "name": "redis"}, call_id="b"),
                tool_call("fetch_pod_logs", {"kind": "pod", "name": "api-5f4d2a1b7-qq8lm"}, call_id="c"),
            ]
        )
        assert path.signatures() == ["logs:pod:api", "topology:service:redis"]

    def test_client_dicts_are_accepted(self):
        path = path_from_tool_calls(
            [tool_call("kubectl_get", {"kind": "svc", "name": "redis"}).to_client_dict()]
        )
        assert path.signatures() == ["topology:service:redis"]

    def test_paused_tool_calls_are_not_checks(self):
        path = path_from_tool_calls(
            [
                tool_call(
                    "kubectl_get",
                    {"kind": "svc"},
                    status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                )
            ]
        )
        assert path.events == []

    def test_failed_checks_are_kept_with_their_failure_class(self):
        path = path_from_tool_calls(
            [
                tool_call(
                    "kubectl_get",
                    {"kind": "svc", "name": "redis"},
                    status=StructuredToolResultStatus.ERROR,
                    error="services 'redis' not found",
                )
            ]
        )
        assert path.events[0].outcome.error_class == ErrorClass.NOT_FOUND

    def test_coarse_signatures_drop_the_entity_name(self):
        path = path_from_tool_calls([tool_call("kubectl_get", {"kind": "svc", "name": "redis"})])
        assert path.signatures(SignatureLevel.COARSE) == ["topology:service"]

    def test_subject_substitution_makes_workloads_comparable(self):
        path = path_from_tool_calls([tool_call("fetch_pod_logs", {"kind": "pod", "name": "orders-service"})])
        assert path.signatures(subject="orders-service") == ["logs:pod:<subject>"]

    def test_no_tool_calls_gives_an_empty_path(self):
        assert path_from_tool_calls(None).events == []


class TestErrorClassification:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Error from server (NotFound)", ErrorClass.NOT_FOUND),
            ("permission denied", ErrorClass.FORBIDDEN),
            ("context deadline exceeded", ErrorClass.TIMEOUT),
            ("parse error at char 3", ErrorClass.INVALID_QUERY),
            ("connection refused", ErrorClass.UNAVAILABLE),
            ("something else entirely", ErrorClass.OTHER),
            (None, ErrorClass.OTHER),
        ],
    )
    def test_classify_error(self, text, expected):
        assert classify_error(text) == expected
