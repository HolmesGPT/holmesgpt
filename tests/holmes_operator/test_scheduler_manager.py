from datetime import datetime, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger

from holmes_operator.context import SchedulerManager
from holmes_operator.models import ScheduledHealthCheckSpec


def _fire_times(trigger: BaseTrigger, start: datetime, end: datetime) -> list[datetime]:
    fire_times = []
    next_fire = trigger.get_next_fire_time(None, start)
    while next_fire is not None and next_fire < end:
        fire_times.append(next_fire)
        next_fire = trigger.get_next_fire_time(next_fire, next_fire)
    return fire_times


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("weekday", "january_days"),
    [
        ("0", [7]),
        ("1-5", [1, 2, 3, 4, 5]),
        ("0,6,wed", [3, 6, 7]),
        ("0-4/2", [2, 4, 7]),
        ("*/2", [2, 4, 6, 7]),
        ("2/2", [2, 4, 6]),
        ("mon-fri", [1, 2, 3, 4, 5]),
        ("*", [1, 2, 3, 4, 5, 6, 7]),
    ],
)
async def test_scheduled_cron_uses_standard_weekdays_and_utc(
    weekday: str, january_days: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "apscheduler.triggers.cron.get_localzone", lambda: ZoneInfo("Asia/Beirut")
    )
    manager = SchedulerManager(timezone_str="UTC", k8s_api=Mock())
    cron = f"0 9 * * {weekday}"
    spec = ScheduledHealthCheckSpec(schedule=cron, query="Check pods")
    job_id = await manager.add_schedule("check", "test", cron, spec, "uid")
    job = manager.scheduler.get_job(job_id)
    assert job is not None
    trigger = job.trigger
    assert isinstance(trigger, CronTrigger)
    assert trigger.timezone == manager.scheduler.timezone

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 8, tzinfo=timezone.utc)
    assert _fire_times(trigger, start, end) == [
        datetime(2024, 1, day, 9, tzinfo=timezone.utc) for day in january_days
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("day_fields", "march_days"),
    [
        ("1 3 1", [1, 2, 9, 16, 23, 30]),
        ("2 3 1", [2, 9, 16, 23, 30]),
        ("1 3 *", [1]),
        ("* 3 1", [2, 9, 16, 23, 30]),
        ("*/2 3 1", [9, 23]),
        ("1 3 */2", [1]),
        ("2 3 */2", []),
    ],
)
async def test_scheduled_cron_combines_day_of_month_and_weekday(
    day_fields: str, march_days: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "apscheduler.triggers.cron.get_localzone", lambda: ZoneInfo("Asia/Beirut")
    )
    manager = SchedulerManager(timezone_str="UTC", k8s_api=Mock())
    cron = f"17 9 {day_fields}"
    spec = ScheduledHealthCheckSpec(schedule=cron, query="Check pods")
    job_id = await manager.add_schedule("check", "test", cron, spec, "uid")
    job = manager.scheduler.get_job(job_id)
    assert job is not None

    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert _fire_times(job.trigger, start, end) == [
        datetime(2026, 3, day, 9, 17, tzinfo=timezone.utc) for day in march_days
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "day_fields",
    [
        "* * 7",
        "* * 5-1",
        "* * */0",
        "* * 1-3/4",
        "* * 1,,2",
        "* * noday",
        "32 3 1",
        "1 3 7",
        "1 13 1",
    ],
)
async def test_scheduled_cron_rejects_invalid_fields(day_fields: str) -> None:
    manager = SchedulerManager(timezone_str="UTC", k8s_api=Mock())
    cron = f"0 9 {day_fields}"
    spec = ScheduledHealthCheckSpec(schedule=cron, query="Check pods")
    with pytest.raises(ValueError, match="Invalid cron expression"):
        await manager.add_schedule("check", "test", cron, spec, "uid")
    assert not manager.scheduler.get_jobs()
