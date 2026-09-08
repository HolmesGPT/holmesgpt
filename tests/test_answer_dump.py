"""Unit tests for the opt-in eval answer dump."""

import os

import pytest
from tests.llm.utils.answer_dump import dump_eval_answer

_VAR = "ANSWER_DUMP_DIR"


def _dump(monkeypatch, directory, **kwargs):
    """Write one answer into `directory` with test defaults, overriding via kwargs."""
    monkeypatch.setenv(_VAR, str(directory))
    dump_eval_answer(
        kwargs.pop("test_id", "290_impact"),
        kwargs.pop("output", "answer"),
        kwargs.pop("correctness", 1),
        **kwargs,
    )


def _files(directory):
    """Every file under `directory`, sorted, so tests can assert on exactly what was written."""
    return sorted(
        os.path.join(root, name)
        for root, _, names in os.walk(directory)
        for name in names
    )


def test_no_op_when_env_var_unset(monkeypatch, tmp_path):
    """Unset ANSWER_DUMP_DIR means a normal run: nothing is written anywhere."""
    monkeypatch.delenv(_VAR, raising=False)
    dump_eval_answer("290_impact", "answer", 1, model="gpt-4.1")
    assert _files(tmp_path) == []


def test_no_op_when_env_var_empty(monkeypatch, tmp_path):
    """An empty ANSWER_DUMP_DIR is treated as unset, not as the current directory."""
    monkeypatch.setenv(_VAR, "")
    dump_eval_answer("290_impact", "answer", 1, model="gpt-4.1")
    assert _files(tmp_path) == []


@pytest.mark.parametrize(
    "correctness,verdict",
    [(1, "pass"), ("1", "pass"), (0, "fail"), (None, "fail"), ("", "fail")],
)
def test_verdict_names_the_file(monkeypatch, tmp_path, correctness, verdict):
    """The verdict prefix follows the harness's int(correctness) == 1 rule, for every shape the score arrives in."""
    _dump(monkeypatch, tmp_path, correctness=correctness)
    assert os.path.basename(_files(tmp_path)[0]).startswith(f"{verdict}-290_impact-")


def test_answer_body_follows_the_metadata_header(monkeypatch, tmp_path):
    """One file carries both the metadata a scorer needs and the answer, header first."""
    _dump(
        monkeypatch,
        tmp_path,
        output="the narrative",
        model="gpt-4.1",
        env_config="live",
    )
    content = open(_files(tmp_path)[0], encoding="utf-8").read()
    assert content.endswith("the narrative")
    assert "model: gpt-4.1" in content
    assert "env_config: live" in content


def test_model_and_env_config_become_directories(monkeypatch, tmp_path):
    """Attribution by path segment, so no scorer can pool two models by
    matching one's name inside the other's."""
    _dump(monkeypatch, tmp_path, model="gpt-4.1", env_config="live")
    _dump(monkeypatch, tmp_path, model="gpt-4.1-mini", env_config="live")
    assert sorted(os.listdir(tmp_path)) == ["gpt-4.1", "gpt-4.1-mini"]
    assert len(_files(tmp_path / "gpt-4.1")) == 1


def test_slashes_in_a_model_name_do_not_nest(monkeypatch, tmp_path):
    """A provider-prefixed model id must stay one directory level, not a tree."""
    _dump(monkeypatch, tmp_path, model="openai/anthropic/claude-opus-4.6")
    assert os.listdir(tmp_path) == ["openai_anthropic_claude-opus-4.6"]


def test_traversal_in_metadata_cannot_escape_the_directory(monkeypatch, tmp_path):
    """Every metadata field is sanitised before it touches the path."""
    root = tmp_path / "dumps"
    _dump(monkeypatch, root, test_id="../../etc/passwd", model="../..", env_config="..")
    written = _files(root)
    assert len(written) == 1
    assert os.path.realpath(written[0]).startswith(os.path.realpath(root))
    # Neutralized into ordinary name characters, so no segment still traverses.
    assert ".." not in os.path.relpath(written[0], root).split(os.sep)


def test_missing_metadata_gets_a_placeholder_directory(monkeypatch, tmp_path):
    """Callers that pass no model or env still get a stable, greppable layout."""
    _dump(monkeypatch, tmp_path)
    assert os.listdir(tmp_path) == ["unknown-model"]
    assert os.listdir(tmp_path / "unknown-model") == ["default"]


def test_long_names_are_bounded(monkeypatch, tmp_path):
    """Each segment is capped so an absurd id cannot exceed filesystem limits."""
    _dump(monkeypatch, tmp_path, test_id="t" * 300, model="m" * 300)
    written = _files(tmp_path)[0]
    assert all(
        len(part) <= 80
        for part in os.path.relpath(written, tmp_path).split(os.sep)[:-1]
    )
    assert len(os.path.basename(written)) < 120


def test_write_failure_is_swallowed(monkeypatch, tmp_path):
    """A measurement aid must never be the reason a test run fails."""
    monkeypatch.setenv(_VAR, str(tmp_path))
    monkeypatch.setattr(
        "tests.llm.utils.answer_dump.os.makedirs",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")),
    )
    dump_eval_answer("290_impact", "answer", 1, model="gpt-4.1")
    assert _files(tmp_path) == []


def test_unstringable_output_is_swallowed(monkeypatch, tmp_path):
    """An output whose __str__ raises is dropped, not propagated."""

    class Hostile:
        def __str__(self):
            raise RuntimeError("no")

    monkeypatch.setenv(_VAR, str(tmp_path))
    dump_eval_answer("290_impact", Hostile(), 1, model="gpt-4.1")
