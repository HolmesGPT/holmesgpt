#!/usr/bin/env bash
# Scenario: MCP healthcheck backoff
#
# Verifies that when an MCP server starts after Holmes, the error message
# is clear and the backoff retry schedule (30s, 60s, 120s) recovers faster
# than the default 300s interval on master.
#
# Called by the CI workflow with:
#   MASTER_ROOT=/tmp/holmes-master BRANCH_ROOT=/path/to/checkout bash scenario.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCENARIOS_DIR="$(dirname "$SCRIPT_DIR")"

MASTER_ROOT="${MASTER_ROOT:?MASTER_ROOT must be set to the master checkout path}"
BRANCH_ROOT="${BRANCH_ROOT:?BRANCH_ROOT must be set to the branch checkout path}"

MCP_PORT=9123
MCP_DELAY=15  # seconds before MCP server starts accepting connections
MAX_SLEEP=45  # cap actual sleep so CI doesn't wait 300s for master
MAX_RETRIES=3

cleanup() {
    echo "Cleaning up..."
    if [ -n "${MCP_PID:-}" ]; then
        kill "$MCP_PID" 2>/dev/null || true
        wait "$MCP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

start_mcp_server() {
    echo "Starting MCP server (delay=${MCP_DELAY}s, port=${MCP_PORT})..."
    poetry -C "$BRANCH_ROOT" run \
        python "$SCRIPT_DIR/mcp_server.py" --delay "$MCP_DELAY" --port "$MCP_PORT" \
        > /dev/null 2>&1 &
    MCP_PID=$!
    echo "MCP server PID: $MCP_PID"
}

stop_mcp_server() {
    if [ -n "${MCP_PID:-}" ]; then
        kill "$MCP_PID" 2>/dev/null || true
        wait "$MCP_PID" 2>/dev/null || true
        unset MCP_PID
        sleep 2  # let port free up
    fi
}

# Extract the last line of stdout (JSON report), ignoring any log lines
# that libraries print to stdout during import.
extract_json() {
    local output_file="$1"
    local json_file="$2"
    # Take the last line that looks like JSON (starts with {)
    grep '^{' "$output_file" | tail -1 > "$json_file"
}

echo "=== Scenario: mcp-healthcheck-backoff ==="
echo "Master root: $MASTER_ROOT"
echo "Branch root: $BRANCH_ROOT"

# Helper: run run.py against a Holmes checkout.
# In CI (virtualenvs.create=false), dependencies are in the system Python.
# Locally, uses poetry run from the checkout dir to get the right venv.
run_scenario() {
    local holmes_root="$1"
    local output_file="$2"
    PYTHONPATH="$holmes_root" poetry -C "$holmes_root" run \
        python "$SCRIPT_DIR/run.py" \
        --mcp-port "$MCP_PORT" --max-sleep "$MAX_SLEEP" --max-retries "$MAX_RETRIES" \
        > "$output_file" 2>&1 || true
}

# --- Run 1: Master ---
echo ""
echo "--- Running against master ---"
start_mcp_server
run_scenario "$MASTER_ROOT" /tmp/scenario-master-raw.txt
extract_json /tmp/scenario-master-raw.txt /tmp/scenario-master.json
stop_mcp_server

echo "Master report:"
cat /tmp/scenario-master.json
echo ""

# --- Run 2: Branch ---
echo ""
echo "--- Running against branch ---"
start_mcp_server
run_scenario "$BRANCH_ROOT" /tmp/scenario-branch-raw.txt
extract_json /tmp/scenario-branch-raw.txt /tmp/scenario-branch.json
stop_mcp_server

echo "Branch report:"
cat /tmp/scenario-branch.json
echo ""

# --- Generate evidence ---
echo ""
echo "--- Generating evidence ---"
poetry -C "$BRANCH_ROOT" run python "$SCENARIOS_DIR/generate_evidence.py" \
    "$SCRIPT_DIR" "$BRANCH_ROOT" \
    /tmp/scenario-master.json /tmp/scenario-branch.json

echo ""
echo "Done. Evidence written to $SCRIPT_DIR/evidence.md"
