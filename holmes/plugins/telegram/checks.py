"""Optional scheduled health checks for the Telegram bot.

A check is a prompt that is sent to Holmes (``POST /api/chat``) at fixed times of the day. The answer must start
with ``<emoji> <SEVERITY> - <check name>``. A check that is not OK is pushed to the allowed chats, an OK check
stays silent. Anything that is not a well-formed answer (HTTP error, no tool call, missing severity header) is
reported as UNKNOWN and never as OK.

The feature is off unless a checks directory is configured (``TELEGRAM_CHECKS_DIR``). It contains a
``schedule.json`` and one prompt file per check::

    {
      "timezone": "Europe/Rome",
      "batches": ["08:30", "20:30"],
      "spacing_minutes": 5,
      "checks": [{"name": "nodes", "file": "nodes.txt"}]
    }

Check *i* of every batch runs at ``batch start + i * spacing_minutes`` in ``timezone`` (DST handled by zoneinfo).
Spacing matters because every check runs a full tool loop and checks never run concurrently.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SEVERITIES = ("OK", "WARNING", "CRITICAL", "UNKNOWN")
SEVERITY_HEADER = re.compile(
    r"^\s*(?:✅|⚠️?|\U0001f6a8|❓)\s*(OK|WARNING|CRITICAL|UNKNOWN)\b"
)

# Appended to every check prompt so that the prompt files only describe *what* to verify.
RESPONSE_CONTRACT = (
    "\n\nFINAL ANSWER FORMAT (mandatory): after running ALL the verifications with the tools, write ONLY the report "
    'described here, with no introduction. First line: "<emoji> <SEVERITY> - {name}", where SEVERITY is the highest '
    "severity found: ✅ OK (check completed, nothing wrong), ⚠️ WARNING (degradation to investigate, no "
    "confirmed critical impact), \U0001f6a8 CRITICAL (active failure or confirmed serious impact), ❓ UNKNOWN (the "
    "check could not be completed or the evidence is insufficient: when data is missing never answer OK). Base the "
    'severity on evidence and impact, not on the word "error" in logs. Then one line per finding (at most 8, each at '
    'most 300 characters): "<emoji> <SEVERITY> | resource | evidence with numbers and UTC timestamps | impact | '
    'probable cause | next check". No tables, no other emoji. Last line: "Coverage: complete" or "Coverage: '
    'incomplete (what is missing)".'
)
RETRY_NUDGE = (
    "\n\nIMPORTANT: a previous attempt ended without a report. Limit the verifications to the essentials (at most "
    "25 tool calls) and write the final report right away in the required format."
)


@dataclass
class Check:
    name: str
    prompt: str
    times: list[str]


@dataclass
class CheckResult:
    text: str
    severity: str
    tool_calls: int
    seconds: float


@dataclass
class ChecksConfig:
    timezone: ZoneInfo
    checks: list[Check]
    retries: int = 1
    retry_delay: int = 30


def load_checks(directory: str) -> ChecksConfig:
    """Load ``schedule.json`` and the prompt files from ``directory``. Raises on a malformed schedule."""
    base = Path(directory)
    spec = json.loads((base / "schedule.json").read_text())
    tz = ZoneInfo(spec.get("timezone", "UTC"))
    spacing = int(spec.get("spacing_minutes", 5))
    batches = spec["batches"]
    checks: list[Check] = []
    for index, entry in enumerate(spec["checks"]):
        times = []
        for batch in batches:
            hour, minute = (int(part) for part in batch.split(":"))
            total = hour * 60 + minute + index * spacing
            times.append(f"{total // 60 % 24:02d}:{total % 60:02d}")
        prompt = (base / entry["file"]).read_text().strip()
        checks.append(Check(name=entry["name"], prompt=prompt, times=times))
    return ChecksConfig(timezone=tz, checks=checks)


def classify(text: str) -> str:
    """Return ``text`` if its first line is ``<emoji> <SEVERITY>``, else an empty string."""
    text = (text or "").strip()
    return text if text and SEVERITY_HEADER.match(text.splitlines()[0]) else ""


def severity_of(text: str) -> str:
    match = SEVERITY_HEADER.match(text.splitlines()[0])
    return match.group(1) if match else "UNKNOWN"


def _unknown(
    name: str, reason: str, detail: str, tools: int, seconds: float
) -> CheckResult:
    text = f"❓ UNKNOWN - {name}: {reason}"
    if detail:
        text += f"\n{detail}"
    return CheckResult(text=text, severity="UNKNOWN", tool_calls=tools, seconds=seconds)


def run_check(
    check: Check,
    ask: Callable[[str], dict[str, Any]],
    retries: int = 1,
    retry_delay: float = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> CheckResult:
    """Run one check through ``ask`` (a function that POSTs to ``/api/chat`` and returns the JSON response)."""
    start = time.time()
    attempts = retries + 1
    data: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        prompt = check.prompt + RESPONSE_CONTRACT.format(name=check.name)
        if attempt > 1:
            prompt += RETRY_NUDGE
        try:
            data = ask(prompt)
            break
        except Exception as exc:  # noqa: BLE001 - any failure must end as UNKNOWN, never as OK
            logger.warning(
                "check %s: attempt %d/%d failed: %s",
                check.name,
                attempt,
                attempts,
                type(exc).__name__,
            )
            if attempt < attempts:
                sleep(retry_delay)
                continue
            return _unknown(
                check.name,
                f"execution failed ({type(exc).__name__}, {attempts} attempts)",
                "",
                0,
                time.time() - start,
            )
    assert data is not None
    seconds = time.time() - start
    tools = sum(
        1 for m in data.get("conversation_history") or [] if m.get("role") == "tool"
    )
    answer = re.sub(
        r"^[\s*#>`_]+", "", (data.get("analysis") or "").replace("**", "")
    ).strip()
    if tools == 0:
        return _unknown(
            check.name,
            "no verification was run (0 tool calls)",
            answer[:600],
            0,
            seconds,
        )
    body = classify(answer)
    if not body:
        return _unknown(
            check.name,
            "answer without a severity header",
            answer[:1500],
            tools,
            seconds,
        )
    return CheckResult(
        text=body, severity=severity_of(body), tool_calls=tools, seconds=seconds
    )


@dataclass
class CheckScheduler:
    """Fires each check at its times. ``run`` executes a check and delivers the result."""

    config: ChecksConfig
    run: Callable[[Check], None]
    stop: threading.Event = field(default_factory=threading.Event)
    _fired: set = field(default_factory=set)

    def due(self, now: datetime) -> list[Check]:
        hhmm = now.strftime("%H:%M")
        due = []
        for check in self.config.checks:
            key: tuple[str, str] = (check.name, now.strftime("%Y%m%d%H%M"))
            if hhmm in check.times and key not in self._fired:
                self._fired.add(key)
                due.append(check)
        if len(self._fired) > 1000:
            self._fired.clear()
        return due

    def loop(self, tick: float = 20) -> None:
        while not self.stop.wait(tick):
            for check in self.due(datetime.now(self.config.timezone)):
                threading.Thread(target=self.run, args=(check,), daemon=True).start()
