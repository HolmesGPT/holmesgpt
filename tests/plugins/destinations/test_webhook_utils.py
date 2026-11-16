
import json

import pytest

from holmes.plugins.destinations.webhook import (
    InvalidWebhookHeadersError,
    resolve_webhook_headers,
)

@pytest.mark.parametrize(
    ("configured_headers", "env_headers", "expected"),
    [
        (
            {"X-Custom": "value", "Authorization": "Bearer token"},
            None,
            {"X-Custom": "value", "Authorization": "Bearer token"},
        ),
        ({"from": "config"}, '{"from": "env"}', {"from": "config"}),
        ({}, '{"from": "env"}', {"from": "env"}),
        (None, '{"X-Test": "value"}', {"X-Test": "value"}),
        (None, None, {}),
        (None, "", {}),
    ],
    ids=[
        "configured-headers",
        "config-takes-precedence",
        "empty-config-falls-back-to-env",
        "parse-valid-env-json",
        "no-config-or-env",
        "empty-env",
    ],
)
def test_resolve_webhook_headers(
    configured_headers, env_headers, expected
):
    assert resolve_webhook_headers(
        configured_headers, env_headers
    ) == expected


@pytest.mark.parametrize(
    "env_headers",
    [
        "{not valid json",
        '["not", "an", "object"]',
        '"just a string"',
        "3",
        "true",
        "null",
    ],
    ids=[
        "invalid-json",
        "array",
        "string",
        "number",
        "boolean",
        "null",
    ],
)
def test_invalid_env_headers_raise_error(env_headers):
    with pytest.raises(InvalidWebhookHeadersError):
        resolve_webhook_headers(None, env_headers)


@pytest.mark.parametrize(
    "env_headers",
    [
        '{"X-Test": 3}',
        '{"X-Test": true}',
        '{"X-Test": null}',
        '{"X-Test": ["one", "two"]}',
        '{"X-Test": {"nested": "value"}}',
        json.dumps({"headers": {"X": "Y"}, "count": 3}),
    ],
    ids=[
        "numeric-value",
        "boolean-value",
        "null-value",
        "array-value",
        "nested-object-value",
        "multiple-invalid-values",
    ],
)
def test_env_headers_reject_non_string_values(env_headers):
    with pytest.raises(InvalidWebhookHeadersError):
        resolve_webhook_headers(None, env_headers)


@pytest.mark.parametrize(
    "configured_headers",
    [
        "not-a-dict",
        ["X: value"],
        {"X-Test": 3},
        {"X-Test": {"nested": "value"}},
    ],
    ids=[
        "string-config",
        "list-config",
        "numeric-value",
        "nested-object-value",
    ],
)
def test_invalid_configured_headers_raise_error(configured_headers):
    with pytest.raises(InvalidWebhookHeadersError):
        resolve_webhook_headers(configured_headers, None)


def test_result_is_not_the_same_object_as_configured_headers():
    configured = {"X": "value"}

    result = resolve_webhook_headers(configured, None)
    result["X"] = "mutated"

    assert configured == {"X": "value"}


def test_resolved_headers_can_be_passed_to_webhook_destination():
    from holmes.plugins.destinations.webhook.plugin import WebhookDestination

    headers = resolve_webhook_headers(None, '{"X-Test": "value"}')
    destination = WebhookDestination(
        method="POST",
        webhook_url="https://hooks.example.com/holmes/checks",
        headers=headers,
    )

    assert destination.headers["X-Test"] == "value"
    assert destination.headers["Content-Type"] == "application/json"
