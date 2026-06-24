"""Unit tests for the SuggestSkills closed-loop eval helpers
(tests/llm/utils/skill_suggestions.py)."""

import os
from types import SimpleNamespace

from tests.llm.utils.skill_suggestions import (
    count_fetch_skill_calls,
    extract_suggested_skills,
    write_suggestions_as_skill_files,
)


def _tool_call(name, params=None, description=""):
    result = SimpleNamespace(params=params) if params is not None else None
    return SimpleNamespace(tool_name=name, result=result, description=description)


SUGGESTION = {
    "title": "Querying app logs in Elasticsearch",
    "symptoms": "Any investigation that searches application logs",
    "instructions": "- Use field `event_ts`, not `@timestamp`\n- Use `svc.keyword` for exact matches",
    "alerts": [],
    "importance": "high",
}


def test_extract_returns_empty_without_tool_calls():
    assert extract_suggested_skills(None) == []
    assert extract_suggested_skills([]) == []


def test_extract_flattens_suggestions_across_calls():
    calls = [
        _tool_call("elasticsearch_search", {"index": "x"}),
        _tool_call("SuggestSkills", {"suggestions": [SUGGESTION]}),
        _tool_call("SuggestSkills", {"suggestions": [dict(SUGGESTION, title="t2")]}),
    ]
    extracted = extract_suggested_skills(calls)
    assert len(extracted) == 2
    assert extracted[0]["title"] == SUGGESTION["title"]
    assert extracted[1]["title"] == "t2"


def test_extract_falls_back_to_description_json():
    calls = [
        _tool_call(
            "SuggestSkills",
            params=None,
            description='SuggestSkills({"suggestions": [{"title": "from desc"}]})',
        )
    ]
    extracted = extract_suggested_skills(calls)
    assert len(extracted) == 1
    assert extracted[0]["title"] == "from desc"


def test_count_fetch_skill_calls():
    calls = [
        _tool_call("fetch_skill", {"name": "a"}),
        _tool_call("elasticsearch_search", {}),
        _tool_call("fetch_skill", {"name": "b"}),
    ]
    assert count_fetch_skill_calls(calls) == 2
    assert count_fetch_skill_calls(None) == 0


def test_write_suggestions_as_skill_files(tmp_path):
    written = write_suggestions_as_skill_files(
        [SUGGESTION, dict(SUGGESTION, title="Second skill", alerts=["KubePodCrashLooping"])],
        str(tmp_path),
    )
    assert len(written) == 2
    for skill_dir in written:
        assert os.path.isfile(os.path.join(skill_dir, "SKILL.md"))

    first = open(os.path.join(written[0], "SKILL.md")).read()
    assert first.startswith("---\n")
    assert "name: 'querying-app-logs-in-elasticsearch'" in first
    # The symptoms field becomes the catalog description the agent sees
    assert "description: 'Any investigation that searches application logs'" in first
    assert "`svc.keyword`" in first

    second = open(os.path.join(written[1], "SKILL.md")).read()
    assert "**Applies to alerts:** KubePodCrashLooping" in second


def test_write_handles_missing_fields(tmp_path):
    written = write_suggestions_as_skill_files([{}], str(tmp_path))
    assert len(written) == 1
    content = open(os.path.join(written[0], "SKILL.md")).read()
    assert "name: 'skill-1'" in content
    assert "**Importance:** medium" in content


def test_write_escapes_quotes_and_newlines_in_description(tmp_path):
    written = write_suggestions_as_skill_files(
        [dict(SUGGESTION, symptoms="it's\nmultiline")], str(tmp_path)
    )
    content = open(os.path.join(written[0], "SKILL.md")).read()
    assert "description: 'it''s multiline'" in content


def test_write_renders_updates_skill_marker(tmp_path):
    written = write_suggestions_as_skill_files(
        [dict(SUGGESTION, updates_skill="app-279-error-log-querying")],
        str(tmp_path),
    )
    content = open(os.path.join(written[0], "SKILL.md")).read()
    assert "**Supersedes skill:** app-279-error-log-querying" in content


def test_write_omits_updates_skill_marker_for_new_skills(tmp_path):
    written = write_suggestions_as_skill_files([SUGGESTION], str(tmp_path))
    content = open(os.path.join(written[0], "SKILL.md")).read()
    assert "Supersedes skill" not in content
