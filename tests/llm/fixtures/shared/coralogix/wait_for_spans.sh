#!/usr/bin/env bash
# Usage: wait_for_spans.sh '<lucene query>'
# Polls Coralogix DataPrime until the query matches MIN_SPANS spans. Only a 200
# with too few spans means "still waiting": any other answer (bad key, rate
# limit, query error, network failure) fails at once with the response, because
# retrying cannot fix it and a silent 0 hides the cause.
set -u

LUCENE_QUERY="${1:-}"
DATAPRIME_URL="${CORALOGIX_DATAPRIME_URL:-https://ng-api-http.eu2.coralogix.com/api/v1/dataprime/query}"
MIN_SPANS="${MIN_SPANS:-10}"
ATTEMPTS="${ATTEMPTS:-60}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-5}"
CURL_MAX_TIME="${CURL_MAX_TIME:-20}"

fail() {
  echo "❌ $1"
  exit 1
}

[ -n "$LUCENE_QUERY" ] || fail "Usage: wait_for_spans.sh '<lucene query>'"
# The query is embedded in a single-quoted DataPrime string inside a JSON string.
case "$LUCENE_QUERY" in
  *\'* | *\"* | *\\*) fail "Lucene query must not contain quotes or backslashes: $LUCENE_QUERY" ;;
esac
[ -n "${CORALOGIX_API_KEY:-}" ] || fail "Missing env var: CORALOGIX_API_KEY"
for name in MIN_SPANS ATTEMPTS INTERVAL_SECONDS CURL_MAX_TIME; do
  [[ "${!name}" =~ ^[1-9][0-9]*$ ]] || fail "$name must be a positive integer, got '${!name}'"
done

RESPONSE_FILE=$(mktemp)
trap 'rm -f "$RESPONSE_FILE"' EXIT

print_body() {
  if [ -s "$RESPONSE_FILE" ]; then
    echo "$1 (first 2000 bytes):"
    head -c 2000 "$RESPONSE_FILE"
    echo
  else
    echo "$1: (empty)"
  fi
}

echo "⏳ Waiting for at least $MIN_SPANS spans matching '$LUCENE_QUERY' in Coralogix (poll every ${INTERVAL_SECONDS}s, $ATTEMPTS attempts)"
for ((i = 1; i <= ATTEMPTS; i++)); do
  CURL_RC=0
  HTTP_CODE=$(curl -sS --max-time "$CURL_MAX_TIME" -o "$RESPONSE_FILE" -w '%{http_code}' \
    -X POST "$DATAPRIME_URL" \
    -H "Authorization: Bearer ${CORALOGIX_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"source spans | lucene '${LUCENE_QUERY}' | limit ${MIN_SPANS}\"}") || CURL_RC=$?

  if [ "$CURL_RC" -ne 0 ] || [ "$HTTP_CODE" != "200" ]; then
    echo "❌ Coralogix DataPrime query failed on attempt $i/$ATTEMPTS: HTTP ${HTTP_CODE}, curl exit ${CURL_RC}"
    print_body "Response body"
    exit 1
  fi

  # Each result row carries one userData field, so this counts spans, not traceID mentions.
  SPAN_COUNT=$(grep -o '"userData"' "$RESPONSE_FILE" | wc -l)
  if [ "$SPAN_COUNT" -ge "$MIN_SPANS" ]; then
    echo "✅ Found $SPAN_COUNT spans in Coralogix"
    exit 0
  fi

  echo "⏳ Attempt $i/$ATTEMPTS: HTTP 200, spans found=$SPAN_COUNT"
  [ "$i" -lt "$ATTEMPTS" ] && sleep "$INTERVAL_SECONDS"
done

echo "❌ Coralogix answered every query with HTTP 200 but fewer than $MIN_SPANS spans after $ATTEMPTS attempts"
print_body "Last response body"
exit 1
