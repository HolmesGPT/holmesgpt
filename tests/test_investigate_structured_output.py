from typing import Any, Optional
import pytest
import json
from holmes.core.investigation_structured_output import (
    DEFAULT_SECTIONS,
    get_output_format_for_investigation,
    parse_json_sections,
    is_response_an_incorrect_tool_call,
    process_response_into_sections,
)
from holmes.plugins.prompts import load_and_render_prompt


def test_prompt_sections_formatting_structured_output():
    issue = {"source_type": "prometheus"}
    prompt = load_and_render_prompt(
        "builtin://generic_investigation.jinja2",
        {"issue": issue, "sections": DEFAULT_SECTIONS},
    )

    print(prompt)
    assert len(DEFAULT_SECTIONS) > 0
    for title, description in DEFAULT_SECTIONS.items():
        assert (
            f"# {title}" in prompt
        ), f'Expected section "{title}" was not found in formatted prompt'
        assert (
            f"{description}" in prompt
        ), f'Expected description for "{title}" was not found in formatted prompt'


def test_prompt_sections_formatting_unstructured_output():
    issue = {"source_type": "prometheus"}
    prompt = load_and_render_prompt(
        "builtin://generic_investigation.jinja2",
        {"issue": issue, "sections": DEFAULT_SECTIONS, "structured_output": True},
    )

    print(prompt)
    assert len(DEFAULT_SECTIONS) > 0
    for title, description in DEFAULT_SECTIONS.items():
        flattened_description = description.replace("\n", "\\n")
        assert (
            f'"{title}": "{flattened_description}"' in prompt
        ), f'Expected section "{title}" not found in formatted prompt'


def test_get_output_format_for_investigation():
    output_format = get_output_format_for_investigation(
        {"Title1": "Description1", "Title2": "Description2"}
    )

    assert output_format
    assert output_format["json_schema"]
    assert output_format["json_schema"]["schema"]
    assert output_format["json_schema"]["schema"]["properties"]

    assert output_format["json_schema"]["schema"]["properties"] == {
        "Title1": {"type": ["string", "null"], "description": "Description1"},
        "Title2": {"type": ["string", "null"], "description": "Description2"},
    }

    assert output_format["json_schema"]["schema"]["required"] == ["Title1", "Title2"]


@pytest.mark.parametrize(
    "input_response,expected_text,expected_sections",
    [
        (
            {"section1": "test1", "section2": "test2"},
            "\n# section1\ntest1\n\n# section2\ntest2\n",
            {"section1": "test1", "section2": "test2"},
        ),
        (
            '{"section1": "test1", "section2": "test2"}',
            "\n# section1\ntest1\n\n# section2\ntest2\n",
            {"section1": "test1", "section2": "test2"},
        ),
        (
            '```json\n{"section1": "test1", "section2": "test2"}\n```',
            "\n# section1\ntest1\n\n# section2\ntest2\n",
            {"section1": "test1", "section2": "test2"},
        ),
        (
            '"{\\n    \\"Next Steps\\": [\\n        \\"1. Verify Prometheus configuration for etcd target\\",\\n        \\"2. Check if the etcd metrics endpoint is accessible\\"],\\n    \\"Conclusions and Possible Root causes\\": [\\n        \\"1. *Missing ServiceMonitor*: No ServiceMonitor was found for the kube-etcd service, which prevents Prometheus from discovering and scraping metrics\\",\\n        \\"2. *Misconfigured Prometheus Scrape Configuration*: The monitoring stack might not be correctly configured to scrape etcd metrics\\"\\n    ]\\n}"',
            "\n# Next Steps\n1. Verify Prometheus configuration for etcd target\n\n2. Check if the etcd metrics endpoint is accessible\n\n# Conclusions and Possible Root causes\n1. *Missing ServiceMonitor*: No ServiceMonitor was found for the kube-etcd service, which prevents Prometheus from discovering and scraping metrics\n\n2. *Misconfigured Prometheus Scrape Configuration*: The monitoring stack might not be correctly configured to scrape etcd metrics\n",
            {
                "Next Steps": "1. Verify Prometheus configuration for etcd target\n\n2. Check if the etcd metrics endpoint is accessible",
                "Conclusions and Possible Root causes": "1. *Missing ServiceMonitor*: No ServiceMonitor was found for the kube-etcd service, which prevents Prometheus from discovering and scraping metrics\n\n2. *Misconfigured Prometheus Scrape Configuration*: The monitoring stack might not be correctly configured to scrape etcd metrics",
            },
        ),
        (123, "123", None),
        (None, "None", None),
        ("plain text", "plain text", None),
        ('{"invalid": json}', '{"invalid": json}', None),
        ([], "[]", None),
        ({}, "{}", None),
        (
            'text here long\n\n```json\n{\n  "section 1": "section 1 text",\n  "section 2": "section 2 text"\n}\n```\n\nanything else here',
            "\n# section 1\nsection 1 text\n\n# section 2\nsection 2 text\n",
            {"section 1": "section 1 text", "section 2": "section 2 text"},
        ),
    ],
)
def test_parse_json_sections(
    input_response: Any, expected_text: str, expected_sections: Optional[dict]
):
    (text, sections) = parse_json_sections(input_response)
    print(f"* ACTUAL\n{text}")
    print(f"* EXPECTED\n{expected_text}")
    assert text == expected_text
    assert sections == expected_sections


@pytest.mark.parametrize(
    "invalid_json", ['{"key": value}', '{key: "value"}', "not json at all"]
)
def test_parse_json_sections_invalid_json(invalid_json):
    result = parse_json_sections(invalid_json)
    assert result == (invalid_json, None)


@pytest.mark.parametrize(
    "response, expected_output",
    [
        (
            # azure AI over litellm
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": '{"timezone":"America/New_York"}',
                    "role": "assistant",
                    "tool_calls": None,
                    "function_call": None,
                },
            },
            True,
        ),
        (
            # Bedrock over litellm
            {
                "finish_reason": "tool_calls",
                "index": 0,
                "message": {
                    "content": '{"kind": "pod", "name": "oomkill-deployment-696dbdbf67-d47z6", "namespace": "default"}',
                    "role": "assistant",
                    "tool_calls": None,
                    "function_call": None,
                    "refusal": None,
                },
            },
            True,
        ),
        (
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": '{"Alert Explanation":"foobar"}',
                    "role": "assistant",
                    "tool_calls": None,
                    "function_call": None,
                },
            },
            False,
        ),
        (
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": {"Alert Explanation": "foobar"},
                    "role": "assistant",
                    "tool_calls": None,
                    "function_call": None,
                },
            },
            False,
        ),
        (
            {
                "finish_reason": "tool_call",
                "index": 0,
                "message": {
                    "content": {"Alert Explanation": "foobar"},
                    "role": "assistant",
                    "tool_calls": None,
                    "function_call": None,
                },
            },
            False,
        ),
    ],
)
def test_is_response_an_incorrect_tool_call(response, expected_output):
    assert (
        is_response_an_incorrect_tool_call(DEFAULT_SECTIONS, response)
        == expected_output
    ), f"Expected the following content to be incorrect={expected_output}. {response}"


TEST_SECTIONS = {
    "Next Steps": "To resolve the issue, correct the command in the init container. Replace 'wge' with 'wget' in the pod's configuration. Apply the changes using:\n```bash\nkubectl apply -f <pod-configuration-file>.yaml\n```",
    "Key Findings": '- The pod `logging-agent` in the `default` namespace is in a `CrashLoopBackOff` state.\n- The init container `downloader` is failing due to a command error: `exec: "wge": executable file not found in $PATH`.\n- The pod has restarted 860 times.',
    "Related logs": 'Logs from pod `logging-agent`:\n```\nError: failed to create containerd task: failed to create shim task: OCI runtime create failed: runc create failed: unable to start container process: exec: "wge": executable file not found in $PATH: unknown\n```',
    "App or Infra?": "This is an application-level issue because the error is due to a misconfigured command in the init container.",
    "Alert Explanation": "The pod `logging-agent` has been in a non-ready state for more than 15 minutes due to a `CrashLoopBackOff` caused by a command error in the init container.",
    "Conclusions and Possible Root causes": "The possible root cause is a typo in the init container's command, where 'wge' should be 'wget'. This prevents the container from starting, leading to the `CrashLoopBackOff` state.",
}


def test_parse_markdown_into_sections_hash_no_leading_line_feed():
    markdown = ""
    for title, content in TEST_SECTIONS.items():
        if not markdown:
            markdown = f"# {title}\n{content}\n"
        else:
            markdown = markdown + f"\n# {title}\n{content}\n"
    (text, sections) = process_response_into_sections(markdown)
    print(markdown)
    print(json.dumps(sections, indent=2))
    assert text == markdown
    assert sections, "None sections returned"
    for title, content in TEST_SECTIONS.items():
        assert title in sections
        assert sections.get(title) == content


def test_parse_markdown_into_sections_hash_leading_line_feed():
    markdown = ""
    for title, content in TEST_SECTIONS.items():
        markdown = markdown + f"\n# {title}\n{content}\n"
    (text, sections) = process_response_into_sections(markdown)
    print(markdown)
    print(json.dumps(sections, indent=2))
    assert text == markdown
    assert sections, "None sections returned"
    for title, content in TEST_SECTIONS.items():
        assert title in sections
        assert sections.get(title) == content


def test_parse_markdown_into_sections_equal():
    markdown = ""
    for title, content in TEST_SECTIONS.items():
        markdown = markdown + f"\n{title}\n==========\n{content}\n"

    (text, sections) = process_response_into_sections(markdown)
    print(markdown)
    print(json.dumps(sections, indent=2))
    assert text == markdown
    assert sections, "None sections returned"
    for title, content in TEST_SECTIONS.items():
        assert title in sections
        assert sections.get(title) == content
