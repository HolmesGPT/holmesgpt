from holmes.plugins.toolsets.opensearch.opensearch_utils import (
    OpenSearchLoggingConfig,
    OpenSearchLoggingLabelsConfig,
    format_logs,
)

config = OpenSearchLoggingConfig(
    opensearch_url="",
    opensearch_auth_header="",
    index_pattern="*",
    labels=OpenSearchLoggingLabelsConfig(),
)

# Sample data derived from the provided query result
SAMPLE_HITS = [
    {
        "_index": "fluentd-2025.05.05",
        "_id": "SFJ9oJYBnT25q51XkZM4",
        "_score": 5.028699,
        "_source": {
            "stream": "stdout",
            "logtag": "F",
            "message": "** ALERTMANAGER_HEADERS={'Content-type': 'application/json', 'X-Scope-Org-Id': '1|2|3|4'}",
            "time": "2025-05-05T12:22:16.745685103Z",
            "docker": {
                "container_id": "4bd5b29e14c6586b07ae8b862d51702c9da997e3c921ad6caeb1983bec8201b4"
            },
            "kubernetes": {
                "container_name": "runner",
                "namespace_name": "default",
                "pod_name": "robusta-runner-796c867f59-wbkpn",
            },
            "@timestamp": "2025-05-05T12:22:16.745685103+00:00",
        },
    },
    {
        "_index": "fluentd-2025.05.05",
        "_id": "SlJ9oJYBnT25q51XkZM4",
        "_score": 5.028699,
        "_source": {
            "stream": "stderr",
            "logtag": "F",
            "message": "\u001b[32m2025-05-05 12:25:16.990 INFO     discovered service with label-selector: `app=kube-prometheus-stack-alertmanager` at url: `http://robusta-kube-prometheus-st-alertmanager.default.svc.cluster.local:9093`\u001b[0m",
            "time": "2025-05-05T12:25:16.990851385Z",
            "docker": {
                "container_id": "4bd5b29e14c6586b07ae8b862d51702c9da997e3c921ad6caeb1983bec8201b4"
            },
            "kubernetes": {
                "container_name": "runner",
                "namespace_name": "default",
                "pod_name": "robusta-runner-796c867f59-wbkpn",
            },
            "@timestamp": "2025-05-05T12:25:16.990851385+00:00",
        },
    },
    {
        "_index": "fluentd-2025.05.05",
        "_id": "missing_fields_id",
        "_score": 5.0,
        "_source": {
            "stream": "stdout",
            # Missing @timestamp, message, log.level
            "time": "2025-05-05T12:30:00.000000000Z",
        },
    },
    {
        "_index": "fluentd-2025.05.05",
        "_id": "non_string_msg_id",
        "_score": 5.0,
        "_source": {
            "@timestamp": "2025-05-05T12:31:00.000000000+00:00",
            "message": 12345,  # Non-string message
        },
    },
]

# --- Test Cases ---


def test_format_logs_empty_input():
    assert format_logs([], config) == ""
    assert format_logs(None, config) == ""


def test_format_logs_invalid_input_items():
    invalid_logs = [
        SAMPLE_HITS[0],  # Valid item
        "not_a_dictionary",
        None,
        {"_id": "no_source_hit"},  # Hit without _source
        {"_id": "bad_source_hit", "_source": "not_a_dict_source"},
    ]
    output = format_logs(invalid_logs, config)
    lines = output.split("\n")
    assert len(lines) == 5
    assert lines[0].startswith("** ALERTMANAGER_HEADERS")  # First is valid
    assert "not_a_dictionary" in lines[1]
    assert "null" in lines[2]
    assert '{"_id": "no_source_hit"}' in lines[3]
    assert '{"_id": "bad_source_hit", "_source": "not_a_dict_source"}' in lines[4]


def test_format_logs_simplified_default():
    output = format_logs(SAMPLE_HITS, config)
    lines = output.split("\n")
    # Current implementation includes a fallback for missing message field
    assert len(lines) == 4
    assert (
        lines[0]
        == "** ALERTMANAGER_HEADERS={'Content-type': 'application/json', 'X-Scope-Org-Id': '1|2|3|4'}"
    )
    assert lines[1].startswith("\u001b[32m2025-05-05 12:25:16.990 INFO")
    # Third entry has missing fields and is presented as JSON
    assert '{"_index": "fluentd-2025.05.05"' in lines[2]
    assert lines[3] == "12345"  # Non-string message converted


def test_format_logs_simplified_custom_fields():
    custom_config = OpenSearchLoggingConfig(
        opensearch_url="",
        opensearch_auth_header="",
        index_pattern="*",
        labels=OpenSearchLoggingLabelsConfig(timestamp="time"),
    )

    output = format_logs(SAMPLE_HITS, custom_config)
    assert "** ALERTMANAGER_HEADERS" in output
