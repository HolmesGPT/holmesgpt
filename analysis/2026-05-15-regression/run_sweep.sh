#!/usr/bin/env bash
# Run 5 iters of the docker-loki regression eval and capture metrics.
# Usage: run_sweep.sh <label>  (writes /tmp/sweep/<label>_iter_<n>.md)
set -e
LABEL="$1"
mkdir -p /tmp/sweep

unset BRAINTRUST_API_KEY OPENAI_BASE_URL
export MODEL=opus-4.6
export MODEL_LIST_FILE_LOCATION=/tmp/model_list.yaml
export CLASSIFIER_MODEL=gpt-4.1
export RUN_LIVE=true
export OPENAI_API_KEY=dummy

for i in 1 2 3 4 5; do
  echo "==== $LABEL iter $i ===="
  # Re-push logs (loki is in-memory but persists across runs of test framework
  # because we skip its cleanup); be defensive anyway
  poetry run python tests/llm/fixtures/test_ask_holmes/259_loki_historical_logs_pod_deleted_docker/generate_logs.py http://localhost:3259 > /dev/null 2>&1 || true

  rm -f evals_report.md
  timeout 300 poetry run pytest tests/llm/test_ask_holmes.py \
    -k "259_loki_historical_logs_pod_deleted_docker" \
    --no-cov -n0 -p no:cacheprovider --skip-setup --skip-cleanup \
    > /tmp/sweep/${LABEL}_iter_${i}.log 2>&1 || true

  if [ -f evals_report.md ]; then
    cp evals_report.md /tmp/sweep/${LABEL}_iter_${i}.md
    grep -E "^\| :|^\| \*\*Total" evals_report.md | head -2
  else
    echo "  (no report)"
    tail -20 /tmp/sweep/${LABEL}_iter_${i}.log
  fi
done
