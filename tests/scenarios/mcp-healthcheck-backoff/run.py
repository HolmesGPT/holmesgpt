"""Scenario runner for MCP healthcheck backoff.

Simulates what happens when Holmes starts and an MCP server is not yet
available.  Records the initial error message, retry intervals, and
time-to-recovery as structured JSON.

This script is run twice by the framework — once against the master
checkout and once against the branch checkout — so the evidence can
show a before/after comparison.

Usage:
    PYTHONPATH=/path/to/holmes python run.py [--mcp-port 9123] [--max-sleep 45]
"""

import argparse
import json
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _build_report(events: list[dict], has_backoff: bool) -> dict:
    return {
        "has_backoff": has_backoff,
        "events": events,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-port", type=int, default=9123)
    parser.add_argument(
        "--max-sleep",
        type=int,
        default=45,
        help="Cap actual sleep to this many seconds (records intended value separately)",
    )
    parser.add_argument("--max-retries", type=int, default=8)
    args = parser.parse_args()

    url = f"http://localhost:{args.mcp_port}"
    events: list[dict] = []

    # --- import from the Holmes codebase on PYTHONPATH ---
    from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

    toolset = RemoteMCPToolset(
        name="test_mcp", description="scenario test", config={"url": url}
    )

    # Detect whether the backoff feature exists (branch) or not (master).
    has_backoff = False
    _get_next_interval = None
    try:
        from server import _get_next_refresh_interval

        _get_next_interval = _get_next_refresh_interval
        has_backoff = True
    except ImportError:
        pass

    # --- initial healthcheck ---
    t0 = time.monotonic()
    ok, msg = toolset.prerequisites_callable(config=toolset.config)
    elapsed = round(time.monotonic() - t0, 1)
    events.append(
        {"type": "initial_check", "elapsed": elapsed, "success": ok, "message": msg}
    )
    logging.info("initial_check  success=%s  msg=%s", ok, msg)

    if ok:
        print(json.dumps(_build_report(events, has_backoff)))
        return

    # --- retry loop ---
    backoff_index = 0
    default_interval = 300  # matches TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS default

    for attempt in range(1, args.max_retries + 1):
        if has_backoff and _get_next_interval is not None:
            intended_sleep, backoff_index = _get_next_interval(
                has_failed_mcp=True,
                backoff_index=backoff_index,
                default_interval=default_interval,
            )
        else:
            intended_sleep = default_interval

        actual_sleep = min(intended_sleep, args.max_sleep)
        events.append(
            {
                "type": "sleep",
                "elapsed": round(time.monotonic() - t0, 1),
                "intended": intended_sleep,
                "actual": actual_sleep,
            }
        )
        logging.info(
            "sleep  intended=%ds  actual=%ds", intended_sleep, actual_sleep
        )
        time.sleep(actual_sleep)

        ok, msg = toolset.prerequisites_callable(config=toolset.config)
        elapsed = round(time.monotonic() - t0, 1)
        events.append(
            {
                "type": "retry",
                "attempt": attempt,
                "elapsed": elapsed,
                "success": ok,
                "message": msg,
            }
        )
        logging.info("retry #%d  elapsed=%.1fs  success=%s", attempt, elapsed, ok)

        if ok:
            events.append({"type": "recovered", "elapsed": elapsed})
            break
    else:
        events.append(
            {"type": "timeout", "elapsed": round(time.monotonic() - t0, 1)}
        )

    print(json.dumps(_build_report(events, has_backoff)))


if __name__ == "__main__":
    main()
