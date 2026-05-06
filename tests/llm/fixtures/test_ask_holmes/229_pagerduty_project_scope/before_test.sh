#!/usr/bin/env bash
set -euo pipefail

PORT=9501
PIDFILE="/tmp/holmes-eval-pd-mock.pid"

if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
fi

python3 "$(dirname "$0")/mock_pagerduty.py" $PORT >/tmp/holmes-eval-pd-mock.log 2>&1 &
echo $! > "$PIDFILE"

for i in {1..50}; do
  if curl -sf "http://127.0.0.1:$PORT/services" >/dev/null 2>&1; then
    echo "Mock PagerDuty server up on :$PORT"
    exit 0
  fi
  sleep 0.1
done

echo "Mock PagerDuty server failed to start — log:" >&2
cat /tmp/holmes-eval-pd-mock.log >&2
exit 1
