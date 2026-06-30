from holmes.core.llm_observability import build_llm_metadata


def test_returns_none_without_context():
    assert build_llm_metadata(None) is None
    assert build_llm_metadata({}) is None


def test_returns_none_when_no_identity_fields():
    # Only unrelated keys (e.g. passthrough headers) → nothing to attribute.
    assert build_llm_metadata({"headers": {"X-Foo": "bar"}}) is None


def test_prefers_user_email_over_user_id():
    md = build_llm_metadata({"user_email": "alice@example.com", "user_id": "u-123"})
    assert md == {"trace_user_id": "alice@example.com"}


def test_falls_back_to_user_id():
    md = build_llm_metadata({"user_id": "u-123"})
    assert md == {"trace_user_id": "u-123"}


def test_maps_conversation_id_to_session():
    md = build_llm_metadata({"conversation_id": "conv-42"})
    assert md == {"session_id": "conv-42"}


def test_builds_tags_from_request_type_and_cluster():
    md = build_llm_metadata(
        {
            "user_id": "u-1",
            "conversation_id": "c-1",
            "request_type": "user_chat",
            "cluster_name": "prod-eu",
        }
    )
    assert md["trace_user_id"] == "u-1"
    assert md["session_id"] == "c-1"
    assert md["tags"] == ["request_type:user_chat", "cluster:prod-eu"]


def test_blank_and_none_values_are_ignored():
    md = build_llm_metadata(
        {"user_email": "  ", "user_id": None, "conversation_id": "c-1"}
    )
    # blank email + None user_id → no trace_user_id, only the session survives.
    assert md == {"session_id": "c-1"}


def test_values_are_stringified_and_stripped():
    md = build_llm_metadata({"user_id": 12345, "conversation_id": "  c-7 "})
    assert md == {"trace_user_id": "12345", "session_id": "c-7"}


def test_tags_are_length_bounded():
    long_cluster = "x" * 1000
    md = build_llm_metadata({"cluster_name": long_cluster})
    assert md is not None
    (tag,) = md["tags"]
    # "cluster:" prefix + truncated value, capped at the 256-char tag bound.
    assert len(tag) == 256
    assert tag.startswith("cluster:xxxx")
