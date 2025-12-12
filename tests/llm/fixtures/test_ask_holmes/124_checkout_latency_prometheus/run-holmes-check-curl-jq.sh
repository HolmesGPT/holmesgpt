#!/usr/bin/env bash

set -euo pipefail

# Requirements: kubectl, jq
# What it does:
# 1) waits for the k6 Job to Complete (fast timeout by default)
# 2) fetches p95(promotions) and p95(none) from Prometheus via kubectl-run+curl
# 3) asserts: p95(promotions) > 2 * p95(none)  AND  p95(none) < 0.20s

NS="${NS:-app-124}"
JOB="${JOB:-k6-coupon-split}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-5m}"
PROM_URL="${PROM_URL:-http://prometheus.${NS}.svc.cluster.local:9090}"

# Short window because Prom scrape/eval is fast in the YAML (5s)
PROMQL_PROMO='histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{route="/checkout",coupon="promotions"}[1m])))'
PROMQL_NONE='histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{route="/checkout",coupon="none"}[1m])))'

echo ">>> Waiting for Job/${JOB} to complete (timeout: ${WAIT_TIMEOUT})"
if ! kubectl -n "${NS}" wait --for=condition=complete "job/${JOB}" --timeout="${WAIT_TIMEOUT}"; then
  echo "ERROR: Job/${JOB} did not complete in time." >&2
  exit 1
fi

# Run curl inside the cluster and stream stdout back; no port-forward
curl_query() {
  local q="$1"
  kubectl run curl-$$ \
    --image=curlimages/curl:8.8.0 \
    --restart=Never --rm -i --quiet -- \
    sh -c "curl -sS --get '${PROM_URL}/api/v1/query' --data-urlencode 'query=${q}'" 2>&1 | \
    awk 'match($0, /^[[:space:]]*{/) { print; exit }'
}

# Simple retry helper to allow last scrapes to land — with verbose error prints
fetch_json() {
  local q="$1" json="" attempt=1

  while [ $attempt -le 10 ]; do
    # Capture stderr from curl_query so we can show it
    local curl_rc curl_stderr tmp
    tmp="$(mktemp)"
    json="$(curl_query "$q" 2>"$tmp")"; curl_rc=$?
    curl_stderr="$(cat "$tmp" 2>/dev/null || true)"; rm -f "$tmp"

    # If curl failed, print its error
    if [ $curl_rc -ne 0 ]; then
      echo "curl_query failed (rc=$curl_rc) on attempt $attempt: $curl_stderr" >&2
    fi

    # If body isn't valid JSON, print jq's parse error + a short preview of the body
    if ! printf '%s' "$json" | jq -e . >/dev/null 2>&1; then
      local jq_err tmpjq
      tmpjq="$(mktemp)"
      printf '%s' "$json" | jq . >/dev/null 2>"$tmpjq" || true
      jq_err="$(cat "$tmpjq" 2>/dev/null || true)"; rm -f "$tmpjq"
      [ -n "$jq_err" ] && echo "jq parse error (attempt $attempt): $jq_err" >&2
      echo "response preview (first 200 bytes): $(printf '%s' "$json" | head -c 200 | tr '\n' ' ')" >&2
    else
      # JSON parsed — check status, and print Prometheus error fields if not success
      local status errType err warns
      status="$(printf '%s' "$json" | jq -r '.status // "UNKNOWN"')"
      if [ "$status" = "success" ]; then
        echo "$json"
        return 0
      else
        errType="$(printf '%s' "$json" | jq -r '.errorType // ""')"
        err="$(printf '%s' "$json" | jq -r '.error // ""')"
        warns="$(printf '%s' "$json" | jq -c '.warnings // []')"
        echo "Prometheus returned status=$status errorType=${errType:-none} error=${err:-none} warnings=${warns}" >&2
      fi
    fi

    echo "Waiting for Prometheus data (attempt $attempt/10)..." >&2
    sleep 3
    attempt=$((attempt+1))
  done

  # Return the last body we saw (even if it failed), so caller can inspect
  echo "$json"
}

echo ">>> Fetching metrics via curl (inside cluster)"
promo_json="$(fetch_json "$PROMQL_PROMO")"
none_json="$(fetch_json "$PROMQL_NONE")"

extract_val() {
  local json="$1"
  local val
  val="$(printf '%s' "$json" | jq -r '.data.result[0].value[1] // empty')" || true
  if [ -z "${val}" ]; then
    echo "ERROR: No numeric value in Prometheus response:" >&2
    echo "$json" >&2
    exit 3
  fi
  printf '%s' "$val"
}

promo_p95="$(extract_val "$promo_json")"
none_p95="$(extract_val "$none_json")"

echo "coupon_p95=${promo_p95}s  nocoupon_p95=${none_p95}s"

# Assertion: promo > 2 * none AND none < 0.20
if awk -v a="$promo_p95" -v b="$none_p95" 'BEGIN{exit !(a > 2*b && b < 0.20)}'; then
  echo ">>> PASS: Only coupon calls are slower (promo p95 > 2x no-promo, baseline p95 < 200ms)."
  exit 0
else
  echo "ERROR: Assertion failed (either promo not >2x no-promo, or baseline >=200ms)." >&2
  # Uncomment for debugging:
  # echo "--- PROMO_JSON ---"; echo "$promo_json"
  # echo "--- NONE_JSON ----"; echo "$none_json"
  exit 2
fi
