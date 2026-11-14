from holmes.plugins.sources.jira import JiraSource


def _source():
    return JiraSource(
        url="https://example.atlassian.net",
        username="user@example.com",
        api_key="token",
        jql_query="project=OPS",
    )


def test_convert_to_issue_without_description():
    # Jira returns "description": null for issues that have no description
    jira_issue = {
        "id": "10001",
        "key": "OPS-1",
        "fields": {"summary": "Pod is crashlooping", "description": None},
    }

    issue = _source().convert_to_issue(jira_issue)

    assert issue.name == "Pod is crashlooping"
    assert issue.description == "No description available."


def test_convert_to_issue_with_description():
    jira_issue = {
        "id": "10002",
        "key": "OPS-2",
        "fields": {
            "summary": "High latency",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "p99 above 2s"}],
                    }
                ],
            },
        },
    }

    issue = _source().convert_to_issue(jira_issue)

    assert issue.description == "p99 above 2s"
