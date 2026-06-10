#!/bin/bash
# 24 single-service namespaces (app-274-<svc>). ALL pods are Running and Ready
# and NO service ever logs at ERROR/WARN level. Three services are silently
# degrading - the signal is a TREND across each full log (latency creep, queue
# backlog growth, cache hit-ratio collapse), not any individual line:
#   - shipping-quote: request latency grows ~20ms -> ~480ms over the log
#   - feed-builder:   queue backlog grows ~40 -> ~1900 over the log
#   - thumbnail-cache: hit ratio collapses 0.97 -> 0.41 over the log
# Anti-shortcut design: every metric's degraded END value is matched by some
# healthy service's STABLE value (sitemap-gen is stable at ~440-470ms,
# rate-meter at backlog ~1850, session-gc at hit_ratio ~0.43), and all
# services log the same line format. So pod status, error-grepping, and
# tail-sampling all look identical between healthy and degraded services -
# only reading each service's log history reveals the degradations.
# Scripts live in Secrets so nothing is visible in pod specs.
set -e

NORMAL_SVCS="search-api media-resizer email-dispatcher geo-lookup session-gc audit-log catalog-api review-svc wishlist-svc shipping-track tax-calc currency-svc loyalty-svc ab-config push-relay sitemap-gen cart-api auth-api rate-meter invoice-api img-api"
DEGRADED_SVCS="shipping-quote feed-builder thumbnail-cache"

mk_service() {
  local NAME="$1"
  local NS="app-274-$2"
  local SCRIPT="$3"

  kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $NS
EOF

  kubectl create secret generic "$NAME-app" -n "$NS" --from-literal=run.sh="$SCRIPT" --dry-run=client -o yaml | kubectl apply -f -

  kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $NAME
  namespace: $NS
  labels:
    app: $NAME
spec:
  replicas: 1
  selector:
    matchLabels:
      app: $NAME
  template:
    metadata:
      labels:
        app: $NAME
    spec:
      containers:
        - name: $NAME
          image: busybox:1.36.1
          command: ["sh", "/opt/app/run.sh"]
          volumeMounts:
            - name: app
              mountPath: /opt/app
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
      volumes:
        - name: app
          secret:
            secretName: $NAME-app
EOF
}

# Healthy service: ~700 lines, all metrics jitter around STABLE values.
# Args: NAME BASE_LATENCY_MS BACKLOG_BASE HIT_RATIO_BASE(2 digits)
healthy_script() {
  local NAME="$1"
  local LAT="$2"
  local BKL="$3"
  local HR="$4"
  cat <<EOS
#!/bin/sh
echo "\$(date -u) INFO  [boot] $NAME starting"
i=1
while [ \$i -le 700 ]; do
  echo "\$(date -u) INFO  [rpc] op=\$((i*13%99991)) status=ok backlog=\$(($BKL + i%35)) hit_ratio=0.\$(($HR + i%3)) \$(($LAT + i%29))ms"
  i=\$((i+1))
done
c=0
while true; do
  c=\$((c+1))
  echo "\$(date -u) INFO  [rpc] op=\$((c*13%99991+100000)) status=ok backlog=\$(($BKL + c%35)) hit_ratio=0.\$(($HR + c%3)) \$(($LAT + c%29))ms"
  sleep 5
done
EOS
}

echo "Creating 21 healthy services..."
# Per-service stable values: "latency backlog hit_ratio"
# sitemap-gen is slow-but-stable (~440-470ms), rate-meter has high-but-stable
# backlog (~1850), session-gc has low-but-stable hit ratio (~0.43) - these
# match the degraded services' end states so tail-sampling cannot discriminate.
healthy_params() {
  case "$1" in
    search-api)       echo "14 5 91" ;;
    media-resizer)    echo "22 12 88" ;;
    email-dispatcher) echo "31 8 93" ;;
    geo-lookup)       echo "9 20 95" ;;
    session-gc)       echo "17 15 43" ;;
    audit-log)        echo "26 25 90" ;;
    catalog-api)      echo "12 10 92" ;;
    review-svc)       echo "35 18 87" ;;
    wishlist-svc)     echo "20 7 94" ;;
    shipping-track)   echo "28 22 89" ;;
    tax-calc)         echo "11 9 96" ;;
    currency-svc)     echo "24 14 91" ;;
    loyalty-svc)      echo "16 11 90" ;;
    ab-config)        echo "33 6 93" ;;
    push-relay)       echo "19 28 88" ;;
    sitemap-gen)      echo "440 30 92" ;;
    cart-api)         echo "13 16 94" ;;
    auth-api)         echo "23 13 95" ;;
    rate-meter)       echo "30 1840 89" ;;
    invoice-api)      echo "15 19 91" ;;
    img-api)          echo "21 24 90" ;;
  esac
}

for SVC in $NORMAL_SVCS; do
  read -r LAT BKL HR <<< "$(healthy_params "$SVC")"
  mk_service "$SVC" "$SVC" "$(healthy_script "$SVC" "$LAT" "$BKL" "$HR")" &
done
wait

echo "Creating 3 silently degrading services..."

# shipping-quote: latency creeps 20ms -> ~480ms across 700 lines, then steady ~480ms
mk_service shipping-quote shipping-quote "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] shipping-quote starting"
i=1
while [ $i -le 700 ]; do
  L=$((20 + i*460/700 + i%13))
  echo "$(date -u) INFO  [rpc] op=$((i*13%99991)) status=ok backlog=$((9 + i%35)) hit_ratio=0.$((90 + i%3)) ${L}ms"
  i=$((i+1))
done
c=0
while true; do
  c=$((c+1))
  echo "$(date -u) INFO  [rpc] op=$((c*13%99991+100000)) status=ok backlog=$((9 + c%35)) hit_ratio=0.$((90 + c%3)) $((480 + c%13))ms"
  sleep 5
done
EOS
)"

# feed-builder: backlog grows 40 -> ~1900 across 700 lines, then steady ~1900
mk_service feed-builder feed-builder "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] feed-builder starting"
i=1
while [ $i -le 700 ]; do
  D=$((40 + i*1860/700 + i%9))
  echo "$(date -u) INFO  [rpc] op=$((i*17%99991)) status=ok backlog=${D} hit_ratio=0.$((91 + i%3)) $((18 + i%21))ms"
  i=$((i+1))
done
c=0
while true; do
  c=$((c+1))
  echo "$(date -u) INFO  [rpc] op=$((c*17%99991+100000)) status=ok backlog=$((1900 + c%9)) hit_ratio=0.$((91 + c%3)) $((18 + c%21))ms"
  sleep 5
done
EOS
)"

# thumbnail-cache: hit ratio collapses 0.97 -> 0.41 across 700 lines, then steady ~0.41
mk_service thumbnail-cache thumbnail-cache "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] thumbnail-cache starting"
i=1
while [ $i -le 700 ]; do
  R=$((97 - i*56/700))
  echo "$(date -u) INFO  [rpc] op=$((i*19%99991)) status=ok backlog=$((12 + i%35)) hit_ratio=0.$R $((6 + i%9))ms"
  i=$((i+1))
done
c=0
while true; do
  c=$((c+1))
  echo "$(date -u) INFO  [rpc] op=$((c*19%99991+100000)) status=ok backlog=$((12 + c%35)) hit_ratio=0.$((41 - c%2)) $((6 + c%9))ms"
  sleep 5
done
EOS
)"

echo "Waiting for all 24 services to be Ready..."
for SVC in $NORMAL_SVCS $DEGRADED_SVCS; do
  OK=false
  for i in $(seq 1 120); do
    if kubectl wait --for=condition=ready pod -l app="$SVC" -n "app-274-$SVC" --timeout=5s 2>/dev/null; then
      OK=true; break
    fi
    sleep 1
  done
  if [ "$OK" = false ]; then
    echo "ERROR: $SVC never became ready"
    kubectl get pods -n "app-274-$SVC"
    exit 1
  fi
done
echo "All 24 services ready"

# Verify the degradation trends are fully written to logs
for PAIR in "shipping-quote:4[89][0-9]ms" "feed-builder:backlog=1[89][0-9][0-9]" "thumbnail-cache:hit_ratio=0.4[0123]"; do
  SVC="${PAIR%%:*}"
  PATTERN="${PAIR##*:}"
  OK=false
  for i in $(seq 1 90); do
    if kubectl logs -n "app-274-$SVC" -l app="$SVC" --tail=-1 2>/dev/null | grep -qE "$PATTERN"; then
      OK=true; break
    fi
    sleep 2
  done
  if [ "$OK" = false ]; then
    echo "ERROR: $SVC degradation trend not present in logs (pattern: $PATTERN)"
    kubectl logs -n "app-274-$SVC" -l app="$SVC" --tail=5
    exit 1
  fi
  echo "$SVC trend present"
done

# Signals must be trend-only: no error-level lines anywhere
for SVC in $DEGRADED_SVCS; do
  if kubectl logs -n "app-274-$SVC" -l app="$SVC" --tail=-1 2>/dev/null | grep -qE "ERROR|WARN|FATAL"; then
    echo "ERROR: $SVC unexpectedly has error-level log lines"
    exit 1
  fi
done

echo "Setup complete: 24 Ready services, 3 silently degrading"
