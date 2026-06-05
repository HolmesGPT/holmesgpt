#!/usr/bin/env bash
# Measurement harness for subagent eval comparison.
# Runs each (eval, mode) N times and collects per-run cost + pass result
# into a TSV at /tmp/measure-results.tsv.
#
# Usage: ./measure_subagent.sh <model> <runs> <evals_csv>
# Example: ./measure_subagent.sh openrouter/openai/gpt-4.1-mini 3 \
#   188_elasticsearch_mapping_explosion,193_elasticsearch_large_mapping_search

set -uo pipefail

MODEL="${1:-openrouter/openai/gpt-4.1-mini}"
RUNS="${2:-3}"
EVALS_CSV="${3:-188_elasticsearch_mapping_explosion}"
OUT="${OUT:-/tmp/measure-results.tsv}"
XML_DIR="${XML_DIR:-/tmp/measure-xml}"

export KUBECONFIG=/tmp/k3s-output/kubeconfig.yaml
export ELASTICSEARCH_URL=http://localhost:9200
export ELASTICSEARCH_API_KEY=dummy-key
export RUN_LIVE=true
unset OPENAI_API_KEY BRAINTRUST_API_KEY BRAINTRUST_SERVICE_TOKEN
export MODEL
export CLASSIFIER_MODEL="openrouter/openai/gpt-4.1"
# Parametrize over BOTH subagent_on and subagent_off so -k filtering selects correctly
export SUBAGENTS="true,false"

mkdir -p "$XML_DIR"

if [ ! -f "$OUT" ]; then
  printf "eval\tmode\trun\tpassed\tcost\ttokens\tprompt_tokens\tcompletion_tokens\tmodel\n" > "$OUT"
fi

IFS=',' read -ra EVAL_LIST <<< "$EVALS_CSV"

# One-time setup for each eval (data only — keep data between mode/run loops)
for eval in "${EVAL_LIST[@]}"; do
  echo ">>> setup: $eval"
  poetry run pytest "tests/llm/test_ask_holmes.py" \
    -k "$eval and subagent_off" --only-setup --no-cov -p no:cacheprovider --skip-cleanup \
    > /tmp/setup-$eval.log 2>&1
  echo "    setup result: $(tail -1 /tmp/setup-$eval.log)"
done

for eval in "${EVAL_LIST[@]}"; do
  for mode in subagent_off subagent_on; do
    for run in $(seq 1 "$RUNS"); do
      XML="$XML_DIR/${eval}-${mode}-run${run}.xml"
      echo ">>> $eval / $mode / run $run"
      poetry run pytest "tests/llm/test_ask_holmes.py" \
        -k "$eval and $mode" \
        --no-cov -p no:cacheprovider --skip-setup --skip-cleanup \
        --junit-xml="$XML" -o junit_family=xunit2 -o junit_log_passing_tests=true \
        > "$XML_DIR/${eval}-${mode}-run${run}.log" 2>&1
      RESULT=$?

      # Parse junit XML for cost/tokens
      python3 -c "
import xml.etree.ElementTree as ET, sys, os
try:
    t = ET.parse('$XML')
    for tc in t.iter('testcase'):
        props = {p.get('name'): p.get('value') for p in tc.iter('property')}
        failure = tc.find('failure')
        skipped = tc.find('skipped')
        if skipped is not None:
            passed = 'SKIP'
        else:
            passed = 'FAIL' if failure is not None else 'PASS'
        print('$eval\t$mode\t$run\t' + passed + '\t' +
              str(props.get('cost', '')) + '\t' +
              str(props.get('total_tokens', '')) + '\t' +
              str(props.get('prompt_tokens', '')) + '\t' +
              str(props.get('completion_tokens', '')) + '\t' +
              '$MODEL')
except Exception as e:
    print('$eval\t$mode\t$run\tERR\t\t\t\t\t$MODEL', file=sys.stderr)
" >> "$OUT" 2>&1
      tail -1 "$OUT"
    done
  done
done

echo
echo "=== Summary ==="
python3 <<PY
import csv
from collections import defaultdict
rows = list(csv.DictReader(open("$OUT"), delimiter='\t'))
agg = defaultdict(lambda: {'pass':0,'fail':0,'cost':[],'tokens':[]})
for r in rows:
    if r['model'] != "$MODEL": continue
    k = (r['eval'], r['mode'])
    if r['passed'] == 'PASS': agg[k]['pass'] += 1
    elif r['passed'] == 'FAIL': agg[k]['fail'] += 1
    try:
        if r['cost']: agg[k]['cost'].append(float(r['cost']))
        if r['tokens']: agg[k]['tokens'].append(int(r['tokens']))
    except: pass

# Per-eval comparison
print(f"{'eval':50s} {'mode':12s} {'pass':>6} {'avg_cost':>12} {'avg_tokens':>12}")
for k,v in sorted(agg.items()):
    runs = v['pass'] + v['fail']
    cost = sum(v['cost'])/len(v['cost']) if v['cost'] else 0
    tok = sum(v['tokens'])/len(v['tokens']) if v['tokens'] else 0
    print(f"{k[0]:50s} {k[1]:12s} {v['pass']}/{runs:<4} \${cost:>10.4f} {tok:>12.0f}")

# Per-eval reduction
print()
print("=== Cost reduction (subagent_on vs subagent_off) ===")
print(f"{'eval':50s} {'cost_off':>10} {'cost_on':>10} {'reduction':>10} {'acc_off':>8} {'acc_on':>8}")
evals = sorted(set(k[0] for k in agg))
for ev in evals:
    off = agg.get((ev,'subagent_off'),{})
    on = agg.get((ev,'subagent_on'),{})
    co = sum(off.get('cost',[]))/max(1,len(off.get('cost',[])))
    cn = sum(on.get('cost',[]))/max(1,len(on.get('cost',[])))
    red = (1 - cn/co)*100 if co else 0
    ao = off.get('pass',0)/max(1,off.get('pass',0)+off.get('fail',0))
    an = on.get('pass',0)/max(1,on.get('pass',0)+on.get('fail',0))
    print(f"{ev:50s} \${co:>8.4f} \${cn:>8.4f} {red:>9.1f}% {ao*100:>7.0f}% {an*100:>7.0f}%")
PY
