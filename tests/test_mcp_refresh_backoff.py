from holmes.common.env_vars import MCP_RETRY_BACKOFF_SCHEDULE
from server import _get_next_refresh_interval


class TestMCPRefreshBackoff:
    def test_backoff_schedule_when_mcp_failed(self):
        """Walks through the full backoff schedule: 30s, 60s, 120s."""
        sleep, idx = _get_next_refresh_interval(has_failed_mcp=True, backoff_index=0, default_interval=300)
        assert sleep == 30
        assert idx == 1

        sleep, idx = _get_next_refresh_interval(has_failed_mcp=True, backoff_index=1, default_interval=300)
        assert sleep == 60
        assert idx == 2

        sleep, idx = _get_next_refresh_interval(has_failed_mcp=True, backoff_index=2, default_interval=300)
        assert sleep == 120
        assert idx == 3

    def test_falls_back_to_default_after_schedule_exhausted(self):
        """After the backoff schedule is exhausted, falls back to the default interval."""
        schedule_len = len(MCP_RETRY_BACKOFF_SCHEDULE)
        sleep, idx = _get_next_refresh_interval(
            has_failed_mcp=True, backoff_index=schedule_len, default_interval=300
        )
        assert sleep == 300
        assert idx == 0

    def test_default_interval_when_no_failures(self):
        """When no MCP servers are failing, always uses the default interval."""
        sleep, idx = _get_next_refresh_interval(has_failed_mcp=False, backoff_index=0, default_interval=300)
        assert sleep == 300
        assert idx == 0

    def test_resets_backoff_when_mcp_recovers(self):
        """Simulates MCP failing then recovering mid-backoff."""
        # First iteration: failed, start backoff
        sleep, idx = _get_next_refresh_interval(has_failed_mcp=True, backoff_index=0, default_interval=300)
        assert sleep == 30
        assert idx == 1

        # MCP recovers — should reset to default
        sleep, idx = _get_next_refresh_interval(has_failed_mcp=False, backoff_index=idx, default_interval=300)
        assert sleep == 300
        assert idx == 0

    def test_backoff_restarts_on_new_failure(self):
        """After recovery and a new failure, backoff restarts from the beginning."""
        # Exhaust backoff, recover, then fail again
        _, idx = _get_next_refresh_interval(has_failed_mcp=True, backoff_index=0, default_interval=300)
        _, idx = _get_next_refresh_interval(has_failed_mcp=False, backoff_index=idx, default_interval=300)
        assert idx == 0  # reset

        # New failure starts from 30s again
        sleep, idx = _get_next_refresh_interval(has_failed_mcp=True, backoff_index=idx, default_interval=300)
        assert sleep == 30
        assert idx == 1
