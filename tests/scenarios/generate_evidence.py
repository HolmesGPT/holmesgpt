#!/usr/bin/env python3
"""Generic scenario evidence generator.

Reads two pre-generated JSON report files (master and branch) and produces
a side-by-side comparison Markdown file.

Usage:
    python generate_evidence.py <scenario_dir> <branch_root> \
        <master_report.json> <branch_report.json>

Infrastructure management and running the scenario against each checkout is
handled by the scenario's own ``scenario.sh``, which calls this script with
the two JSON report files after both runs complete.

Output:
  - <scenario_dir>/evidence.md
"""

import datetime
import json
import os
import subprocess
import sys
import textwrap


def _format_events_table(events: list[dict]) -> str:
    rows = ["| Elapsed | Event | Detail |", "|---------|-------|--------|"]
    for e in events:
        t = f"T+{e.get('elapsed', '?')}s"
        etype = e["type"]
        if etype == "initial_check":
            ok = "pass" if e["success"] else "FAIL"
            detail = f"`{e['message']}`" if e.get("message") else ""
            rows.append(f"| {t} | Healthcheck ({ok}) | {detail} |")
        elif etype == "sleep":
            intended = e["intended"]
            actual = e["actual"]
            note = "" if intended == actual else f" (capped from {intended}s)"
            rows.append(f"| {t} | Wait {actual}s{note} | |")
        elif etype == "retry":
            ok = "pass" if e["success"] else "FAIL"
            detail = (
                f"`{e['message']}`" if e.get("message") and not e["success"] else ""
            )
            rows.append(f"| {t} | Retry #{e['attempt']} ({ok}) | {detail} |")
        elif etype == "recovered":
            rows.append(f"| {t} | Recovered | |")
        elif etype == "timeout":
            rows.append(f"| {t} | Timeout (did not recover) | |")
    return "\n".join(rows)


def _recovery_time(events: list[dict]) -> str:
    for e in events:
        if e["type"] == "recovered":
            return f"{e['elapsed']}s"
    return "did not recover"


def _first_error(events: list[dict]) -> str:
    for e in events:
        if e["type"] == "initial_check" and not e.get("success"):
            return e.get("message", "")
    return ""


def _first_retry_interval(events: list[dict]) -> str:
    for e in events:
        if e["type"] == "sleep":
            return f"{e['intended']}s"
    return "N/A"


def generate(
    scenario_dir: str,
    branch_root: str,
    master_report: dict,
    branch_report: dict,
) -> str:
    scenario_name = os.path.basename(os.path.normpath(scenario_dir))
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    branch_name = ""
    commit_sha = ""
    try:
        branch_name = subprocess.check_output(
            ["git", "-C", branch_root, "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
        commit_sha = subprocess.check_output(
            ["git", "-C", branch_root, "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        pass

    master_error = _first_error(master_report["events"])
    branch_error = _first_error(branch_report["events"])

    md = textwrap.dedent(f"""\
    ## Scenario: {scenario_name}

    Generated: {now}
    Branch: `{branch_name}` (`{commit_sha}`)

    ### Summary

    | | Master | Branch |
    |---|--------|--------|
    | Error message quality | `{master_error[:80]}{"..." if len(master_error) > 80 else ""}` | `{branch_error[:80]}{"..." if len(branch_error) > 80 else ""}` |
    | First retry interval | {_first_retry_interval(master_report["events"])} | {_first_retry_interval(branch_report["events"])} |
    | Time to recovery | {_recovery_time(master_report["events"])} | {_recovery_time(branch_report["events"])} |
    | Has backoff | {master_report["has_backoff"]} | {branch_report["has_backoff"]} |

    ### Master timeline

    {_format_events_table(master_report["events"])}

    ### Branch timeline

    {_format_events_table(branch_report["events"])}
    """)

    return md


def main():
    if len(sys.argv) != 5:
        print(
            f"Usage: {sys.argv[0]} <scenario_dir> <branch_root>"
            " <master_report.json> <branch_report.json>",
            file=sys.stderr,
        )
        sys.exit(1)

    scenario_dir = sys.argv[1]
    branch_root = sys.argv[2]

    with open(sys.argv[3]) as f:
        master_report = json.load(f)
    with open(sys.argv[4]) as f:
        branch_report = json.load(f)

    md = generate(scenario_dir, branch_root, master_report, branch_report)

    evidence_path = os.path.join(scenario_dir, "evidence.md")
    with open(evidence_path, "w") as f:
        f.write(md)

    print(f"Evidence written to {evidence_path}", file=sys.stderr)
    print(md)


if __name__ == "__main__":
    main()
