import hashlib

from holmes.core.llm_observability import (
    TraceAttribution,
    build_trace_attribution,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_empty_without_context():
    assert build_trace_attribution(None).is_empty()
    assert build_trace_attribution({}).is_empty()


def test_empty_when_no_identity_fields():
    # Only unrelated keys (e.g. passthrough headers) → nothing to attribute.
    attr = build_trace_attribution({"headers": {"X-Foo": "bar"}})
    assert attr.is_empty()


def test_prefers_user_email_over_user_id():
    attr = build_trace_attribution(
        {"user_email": "alice@example.com", "user_id": "u-123"}
    )
    assert attr.user == _hash("alice@example.com")
    assert attr.metadata is None


def test_falls_back_to_user_id():
    attr = build_trace_attribution({"user_id": "u-123"})
    assert attr.user == _hash("u-123")
    assert attr.metadata is None


def test_user_is_hashed_not_raw():
    # The raw identifier must never end up in the attribution sent to the
    # model provider — only a stable hash of it.
    attr = build_trace_attribution({"user_email": "alice@example.com"})
    assert attr.user != "alice@example.com"
    assert attr.user == hashlib.sha256(b"alice@example.com").hexdigest()


def test_user_hash_is_deterministic():
    attr1 = build_trace_attribution({"user_email": "alice@example.com"})
    attr2 = build_trace_attribution({"user_email": "alice@example.com"})
    assert attr1.user == attr2.user


def test_maps_conversation_id_to_session():
    attr = build_trace_attribution({"conversation_id": "conv-42"})
    assert attr.user is None
    assert attr.metadata == {"session_id": "conv-42"}


def test_builds_tags_from_request_type_and_cluster():
    attr = build_trace_attribution(
        {
            "user_id": "u-1",
            "conversation_id": "c-1",
            "request_type": "user_chat",
            "cluster_name": "prod-eu",
        }
    )
    assert attr.user == _hash("u-1")
    assert attr.metadata == {
        "session_id": "c-1",
        "tags": ["request_type:user_chat", "cluster:prod-eu"],
    }


def test_blank_and_none_values_are_ignored():
    attr = build_trace_attribution(
        {"user_email": "  ", "user_id": None, "conversation_id": "c-1"}
    )
    # blank email + None user_id → no user, only the session survives.
    assert attr.user is None
    assert attr.metadata == {"session_id": "c-1"}


def test_values_are_stringified_and_stripped():
    attr = build_trace_attribution({"user_id": 12345, "conversation_id": "  c-7 "})
    assert attr.user == _hash("12345")
    assert attr.metadata == {"session_id": "c-7"}


def test_tags_are_length_bounded():
    attr = build_trace_attribution({"cluster_name": "x" * 1000})
    assert attr.metadata is not None
    (tag,) = attr.metadata["tags"]
    # "cluster:" prefix + truncated value, capped at the 256-char tag bound.
    assert len(tag) == 256
    assert tag.startswith("cluster:xxxx")


def test_is_empty_helper():
    assert TraceAttribution().is_empty()
    assert not TraceAttribution(user="u").is_empty()
    assert not TraceAttribution(metadata={"session_id": "s"}).is_empty()
