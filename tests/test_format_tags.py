import pytest

from holmes.utils.tags import format_tags_in_string, parse_messages_tags


@pytest.mark.parametrize(
    "input, expected_output",
    [
        (
            'What is the status of << { "type": "service", "namespace": "default", "kind": "Deployment", "name": "nginx" } >>?',
            "What is the status of service nginx (namespace=default, kind=Deployment)?",
        ),
        (
            'why did << { "type": "job", "namespace": "my-namespace", "name": "my-job" } >> fail?',
            "why did job my-job (namespace=my-namespace) fail?",
        ),
        (
            'why did << { "type": "pod", "namespace": "my-namespace", "name": "runner-2323" } >> fail?',
            "why did pod runner-2323 (namespace=my-namespace) fail?",
        ),
        (
            'how many pods are running on << { "type": "node", "name": "my-node" } >>?',
            "how many pods are running on node my-node?",
        ),
        (
            'What caused << { "type": "issue", "id": "issue-id", "name": "KubeJobFailed", "subject_namespace": "my-namespace", "subject_name": "my-pod" } >>?',
            "What caused issue issue-id (name=KubeJobFailed, subject_namespace=my-namespace, subject_name=my-pod)?",
        ),
        (
            'tell me about << {"type":"service","namespace":"sock-shop","kind":"Deployment","name":"carts"} >> and << { "type": "node", "name": "my-node" } >> and << {"type":"service","namespace":"sock-shop","kind":"Deployment","name":"front-end"} >>',
            "tell me about service carts (namespace=sock-shop, kind=Deployment) and node my-node and service front-end (namespace=sock-shop, kind=Deployment)",
        ),
    ],
)
def test_format_tags_in_string(input, expected_output):
    assert format_tags_in_string(input) == expected_output


def test_parse_message_tags():
    assert parse_messages_tags(
        [
            {
                "role": "user",
                "content": 'how many pods are running on << { "type": "node", "name": "my-node" } >>?',
            }
        ]
    )[0] == {"role": "user", "content": "how many pods are running on node my-node?"}


def test_parse_message_tags_multimodal_with_tags():
    """Multimodal content (text + image) with tags in text should format tags and preserve image blocks."""
    original_content = [
        {
            "type": "text",
            "text": 'What is wrong with << { "type": "service", "namespace": "default", "kind": "Deployment", "name": "nginx" } >>?',
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
    ]
    result = parse_messages_tags(
        [{"role": "user", "content": original_content}]
    )
    expected_content = [
        {
            "type": "text",
            "text": "What is wrong with service nginx (namespace=default, kind=Deployment)?",
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
    ]
    assert result[0]["content"] == expected_content


def test_parse_message_tags_multimodal_without_tags():
    """Multimodal content without tags should pass through unchanged (same object)."""
    original_content = [
        {"type": "text", "text": "Analyze this image"},
        {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
    ]
    messages = [{"role": "user", "content": original_content}]
    result = parse_messages_tags(messages)
    assert result[0]["content"] is original_content


def test_parse_message_tags_multimodal_image_only():
    """Multimodal content with only image blocks (no text) should pass through unchanged."""
    original_content = [
        {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
    ]
    messages = [{"role": "user", "content": original_content}]
    result = parse_messages_tags(messages)
    assert result[0]["content"] is original_content
