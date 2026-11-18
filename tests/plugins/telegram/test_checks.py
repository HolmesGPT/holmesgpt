import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from holmes.plugins.telegram.checks import (
    Check,
    CheckScheduler,
    classify,
    load_checks,
    run_check,
)

TOOL = {"role": "tool"}


def make_ask(analysis, tools=1, fail_times=0):
    state = {"calls": 0, "prompts": []}

    def ask(prompt):
        state["calls"] += 1
        state["prompts"].append(prompt)
        if state["calls"] <= fail_times:
            raise RuntimeError("boom")
        return {"analysis": analysis, "conversation_history": [TOOL] * tools}

    ask.state = state
    return ask


CHECK = Check(name="cluster-pods", prompt="look at pods", times=["08:00"])


def test_ok_answer_is_ok():
    result = run_check(CHECK, make_ask("✅ OK - cluster-pods\nCoverage: complete"))
    assert result.severity == "OK" and result.tool_calls == 1


@pytest.mark.parametrize(
    "answer,sev",
    [
        ("⚠️ WARNING - x\n...", "WARNING"),
        ("\U0001f6a8 CRITICAL - x\n...", "CRITICAL"),
        ("**✅ OK - x**", "OK"),  # markdown noise is stripped
    ],
)
def test_severity_is_parsed(answer, sev):
    assert run_check(CHECK, make_ask(answer)).severity == sev


def test_answer_without_header_is_unknown_not_ok():
    result = run_check(CHECK, make_ask("everything looks fine"))
    assert result.severity == "UNKNOWN"


def test_zero_tool_calls_is_unknown_even_if_answer_says_ok():
    result = run_check(CHECK, make_ask("✅ OK - cluster-pods", tools=0))
    assert result.severity == "UNKNOWN"


def test_failure_is_retried_with_a_nudge_then_succeeds():
    ask = make_ask("✅ OK - cluster-pods", fail_times=1)
    result = run_check(CHECK, ask, retries=1, retry_delay=0, sleep=lambda _: None)
    assert result.severity == "OK"
    assert "IMPORTANT" not in ask.state["prompts"][0]
    assert "IMPORTANT" in ask.state["prompts"][1]


def test_exhausted_retries_is_unknown():
    result = run_check(
        CHECK,
        make_ask("x", fail_times=5),
        retries=1,
        retry_delay=0,
        sleep=lambda _: None,
    )
    assert result.severity == "UNKNOWN" and "2 attempts" in result.text


def test_prompt_carries_the_response_contract_with_the_check_name():
    ask = make_ask("✅ OK - cluster-pods")
    run_check(CHECK, ask)
    assert "look at pods" in ask.state["prompts"][0]
    assert "<emoji> <SEVERITY> - cluster-pods" in ask.state["prompts"][0]


def test_classify():
    assert classify("✅ OK - a") and not classify("OK - a") and not classify("")


def test_load_checks_spreads_checks_inside_a_batch(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    (tmp_path / "schedule.json").write_text(
        json.dumps(
            {
                "timezone": "Europe/Rome",
                "batches": ["08:00", "23:58"],
                "spacing_minutes": 5,
                "checks": [
                    {"name": "a", "file": "a.txt"},
                    {"name": "b", "file": "b.txt"},
                ],
            }
        )
    )
    cfg = load_checks(str(tmp_path))
    assert cfg.timezone.key == "Europe/Rome"
    assert cfg.checks[0].times == ["08:00", "23:58"]
    assert cfg.checks[1].times == ["08:05", "00:03"]  # wraps past midnight
    assert cfg.checks[1].prompt == "B"


def test_example_checks_load():
    from pathlib import Path

    import holmes.plugins.telegram as pkg

    cfg = load_checks(str(Path(pkg.__file__).parent / "example_checks"))
    assert [c.name for c in cfg.checks] == [
        "cluster-nodes",
        "cluster-pods",
        "cluster-events",
    ]
    assert all(c.prompt for c in cfg.checks)


def test_scheduler_fires_each_check_once_per_minute():
    cfg = load_checks_for(["08:00"])
    fired = []
    sched = CheckScheduler(config=cfg, run=fired.append)
    now = datetime(2026, 1, 1, 8, 0, 5, tzinfo=ZoneInfo("UTC"))
    assert [c.name for c in sched.due(now)] == ["a"]
    assert sched.due(now.replace(second=25)) == []  # same minute, already fired
    assert sched.due(now.replace(minute=1)) == []


def load_checks_for(times):
    from holmes.plugins.telegram.checks import ChecksConfig

    return ChecksConfig(timezone=ZoneInfo("UTC"), checks=[Check("a", "p", times)])
