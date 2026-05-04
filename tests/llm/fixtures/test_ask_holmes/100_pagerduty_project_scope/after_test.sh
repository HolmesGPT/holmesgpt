#!/usr/bin/env bash
PIDFILE="/tmp/holmes-eval-pd-mock.pid"
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
fi
exit 0
