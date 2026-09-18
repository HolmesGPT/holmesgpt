"""Regression: ScheduledHealthCheck destinations are copied to child HealthChecks."""

from holmes_operator.models import (
    CheckMode,
    DestinationConfig,
    ScheduledHealthCheckSpec,
)
from holmes_operator.scheduler.job_executor import _generate_healthcheck_object


def test_spawned_healthcheck_preserves_slack_thread_ts() -> None:
    spec = ScheduledHealthCheckSpec(
        schedule="0 * * * *",
        query="Is prod healthy?",
        mode=CheckMode.ALERT,
        destinations=[
            DestinationConfig(
                type="slack",
                config={
                    "channel": "#alerts",
                    "thread_ts": "1715098123.000200",
                },
            )
        ],
    )
    hc = _generate_healthcheck_object(
        check_name="sched-abc",
        namespace="prod",
        name="hourly",
        scheduled_uid="uid-1",
        spec=spec,
    )
    dests = hc["spec"]["destinations"]
    assert len(dests) == 1
    assert dests[0]["type"] == "slack"
    assert dests[0]["config"]["channel"] == "#alerts"
    assert dests[0]["config"]["thread_ts"] == "1715098123.000200"
