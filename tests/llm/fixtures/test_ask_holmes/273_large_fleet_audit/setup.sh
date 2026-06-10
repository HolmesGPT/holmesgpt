#!/bin/bash
# Creates 24 single-service namespaces (app-273-<svc>): 6 planted failures and
# 18 healthy noisy controls. App scripts are stored in Secrets so verification
# codes are only discoverable through logs, never through pod specs.
set -e

HEALTHY_SVCS="search-api media-resizer email-dispatcher geo-lookup session-gc audit-log catalog-api review-svc wishlist-svc shipping-quote tax-calc currency-svc loyalty-svc feed-builder ab-config push-relay sitemap-gen thumbnail-cache"
BROKEN_SVCS="auth-svc cart-svc rate-limiter invoice-gen geo-cache img-optimizer"

mk_service() {
  local NAME="$1"
  local NS="app-273-$2"
  local SCRIPT="$3"
  local EXTRA_CONTAINER_YAML="$4"

  kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $NS
EOF

  if [ -n "$SCRIPT" ]; then
    kubectl create secret generic "$NAME-app" -n "$NS" --from-literal=run.sh="$SCRIPT" --dry-run=client -o yaml | kubectl apply -f -
  fi

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
$EXTRA_CONTAINER_YAML
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
      volumes:
        - name: app
          secret:
            secretName: $NAME-app
            optional: true
EOF
}

# Generic healthy-service script: ~500 varied INFO lines at boot, then steady varied lines
healthy_script() {
  local NAME="$1"
  local OP="$2"
  cat <<EOS
#!/bin/sh
echo "\$(date -u) INFO  [boot] $NAME starting"
i=1
while [ \$i -le 500 ]; do
  echo "\$(date -u) INFO  [$OP] op=\$((i*13%99991)) status=ok shard=\$((i%8)) \$((i%47+3))ms"
  i=\$((i+1))
done
c=0
while true; do
  c=\$((c+1))
  echo "\$(date -u) INFO  [$OP] op=\$((c*13%99991+100000)) status=ok shard=\$((c%8)) \$((c%47+3))ms"
  sleep 5
done
EOS
}

RUN_CMD='          command: ["sh", "/opt/app/run.sh"]
          volumeMounts:
            - name: app
              mountPath: /opt/app'

echo "Creating 18 healthy services..."
for SVC in $HEALTHY_SVCS; do
  mk_service "$SVC" "$SVC" "$(healthy_script "$SVC" worker)" "$RUN_CMD" &
done
wait

echo "Creating 6 broken services..."

# 1. auth-svc: crashloop - KMS endpoint unreachable. Code ERR-AUTH-92c4e1 in logs.
mk_service auth-svc auth "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] auth-svc 4.2.0 starting"
i=1
while [ $i -le 400 ]; do
  echo "$(date -u) INFO  [token] issued jwt sub=u$((i*3%4200)) scope=api $((i%19+2))ms"
  i=$((i+1))
done
sleep 15
echo "$(date -u) ERROR [kms] connect to token-signing endpoint kms.internal:8200 failed (attempt 1/3)"
echo "$(date -u) ERROR [kms] connect to token-signing endpoint kms.internal:8200 failed (attempt 2/3)"
echo "$(date -u) FATAL [kms] ERR-AUTH-92c4e1 cannot reach token-signing KMS endpoint kms.internal:8200: connection refused - exiting"
exit 1
EOS
)" "$RUN_CMD"

# 2. cart-svc: Running AND Ready, but snapshots silently dropped. Code ERR-CART-55ab07.
mk_service cart-svc cart "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] cart-svc 2.9.1 starting"
i=1
while [ $i -le 500 ]; do
  echo "$(date -u) INFO  [cart] updated cart c-$((i*7%9973)) items=$((i%12+1)) $((i%31+4))ms"
  if [ $((i % 55)) -eq 0 ]; then
    echo "$(date -u) ERROR [persist] ERR-CART-55ab07 failed to persist cart snapshot: quota exceeded on bucket cart-snapshots - snapshot DROPPED, carts at risk on restart"
  fi
  i=$((i+1))
done
c=0
while true; do
  c=$((c+1))
  echo "$(date -u) INFO  [cart] updated cart c-$((c*7%9973+10000)) items=$((c%12+1)) $((c%31+4))ms"
  if [ $((c % 9)) -eq 0 ]; then
    echo "$(date -u) ERROR [persist] ERR-CART-55ab07 failed to persist cart snapshot: quota exceeded on bucket cart-snapshots - snapshot DROPPED, carts at risk on restart"
  fi
  sleep 2
done
EOS
)" "$RUN_CMD"

# 3. rate-limiter: Running AND Ready, but policy engine failing open. Code ERR-RATE-c813f9.
mk_service rate-limiter ratelimit "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] rate-limiter 1.6.3 starting, policy bundle rev 4187"
i=1
while [ $i -le 500 ]; do
  echo "$(date -u) INFO  [check] verdict=allow key=k-$((i*11%9967)) budget_left=$((i*7%1000)) $((i%5+1))ms"
  if [ $((i % 65)) -eq 0 ]; then
    echo "$(date -u) ERROR [policy] ERR-RATE-c813f9 lua sandbox out of memory evaluating policy bundle rev 4187 - FALLING BACK TO ALLOW-ALL, rate limits NOT enforced"
  fi
  i=$((i+1))
done
c=0
while true; do
  c=$((c+1))
  echo "$(date -u) INFO  [check] verdict=allow key=k-$((c*11%9967+10000)) budget_left=$((c*7%1000)) $((c%5+1))ms"
  if [ $((c % 10)) -eq 0 ]; then
    echo "$(date -u) ERROR [policy] ERR-RATE-c813f9 lua sandbox out of memory evaluating policy bundle rev 4187 - FALLING BACK TO ALLOW-ALL, rate limits NOT enforced"
  fi
  sleep 2
done
EOS
)" "$RUN_CMD"

# 4. invoice-gen: Running but NOT ready (renderer pool dead). Code ERR-INVC-d27b44.
mk_service invoice-gen invoices "$(cat <<'EOS'
#!/bin/sh
echo "$(date -u) INFO  [boot] invoice-gen 3.1.4 starting, renderer pool size=4"
i=1
while [ $i -le 450 ]; do
  echo "$(date -u) INFO  [queue] invoice job j-$((i*17%9931)) queued depth=$((i%40))"
  if [ $((i % 70)) -eq 0 ]; then
    echo "$(date -u) ERROR [render] ERR-INVC-d27b44 PDF renderer pool exhausted: 0/4 workers alive - jobs stalling, readiness withheld"
  fi
  i=$((i+1))
done
c=0
while true; do
  c=$((c+1))
  echo "$(date -u) INFO  [queue] invoice job j-$((c*17%9931+10000)) queued depth=$((c%40+40))"
  if [ $((c % 10)) -eq 0 ]; then
    echo "$(date -u) ERROR [render] ERR-INVC-d27b44 PDF renderer pool exhausted: 0/4 workers alive - jobs stalling, readiness withheld"
  fi
  sleep 2
done
EOS
)" '          command: ["sh", "/opt/app/run.sh"]
          readinessProbe:
            exec:
              command: ["sh", "-c", "test -f /tmp/renderer-ready"]
            initialDelaySeconds: 5
            periodSeconds: 5
          volumeMounts:
            - name: app
              mountPath: /opt/app'

# 5. geo-cache: CreateContainerConfigError - missing ConfigMap geo-cache-settings
mk_service geo-cache geocache "" '          command: ["sh", "-c", "sleep infinity"]
          envFrom:
            - configMapRef:
                name: geo-cache-settings'

# 6. img-optimizer: image pull failure (nonexistent image)
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: app-273-imgopt
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: img-optimizer
  namespace: app-273-imgopt
  labels:
    app: img-optimizer
spec:
  replicas: 1
  selector:
    matchLabels:
      app: img-optimizer
  template:
    metadata:
      labels:
        app: img-optimizer
    spec:
      containers:
        - name: img-optimizer
          image: ghcr.io/acme-platform/img-optimizer:6.0.2
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
EOF

echo "Waiting for planted states..."

# healthy services + the two Ready-but-erroring ones must be Ready
for SVC in $HEALTHY_SVCS; do
  OK=false
  for i in $(seq 1 120); do
    if kubectl wait --for=condition=ready pod -l app="$SVC" -n "app-273-$SVC" --timeout=5s 2>/dev/null; then
      OK=true; break
    fi
    sleep 1
  done
  if [ "$OK" = false ]; then
    echo "ERROR: $SVC never became ready"
    kubectl get pods -n "app-273-$SVC"
    exit 1
  fi
done
echo "18 healthy services ready"

for PAIR in "cart-svc:app-273-cart:ERR-CART-55ab07" "rate-limiter:app-273-ratelimit:ERR-RATE-c813f9"; do
  SVC="${PAIR%%:*}"; REST="${PAIR#*:}"; NS="${REST%%:*}"; CODE="${REST##*:}"
  OK=false
  for i in $(seq 1 90); do
    if kubectl wait --for=condition=ready pod -l app="$SVC" -n "$NS" --timeout=5s 2>/dev/null; then
      if kubectl logs -n "$NS" -l app="$SVC" --tail=-1 2>/dev/null | grep -q "$CODE"; then
        OK=true; break
      fi
    fi
    sleep 2
  done
  if [ "$OK" = false ]; then
    echo "ERROR: $SVC never reached Ready with $CODE in logs"
    kubectl get pods -n "$NS"
    exit 1
  fi
  echo "$SVC ready but logging $CODE"
done

# invoice-gen: Running but NOT ready with code in logs
OK=false
for i in $(seq 1 120); do
  PHASE=$(kubectl get pods -n app-273-invoices -l app=invoice-gen -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)
  READY=$(kubectl get pods -n app-273-invoices -l app=invoice-gen -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || true)
  if [ "$PHASE" = "Running" ] && [ "$READY" = "false" ]; then
    if kubectl logs -n app-273-invoices -l app=invoice-gen --tail=-1 2>/dev/null | grep -q "ERR-INVC-d27b44"; then
      OK=true; break
    fi
  fi
  sleep 2
done
if [ "$OK" = false ]; then
  echo "ERROR: invoice-gen never reached Running+NotReady with code in logs"
  kubectl get pods -n app-273-invoices
  exit 1
fi
echo "invoice-gen running but not ready"

# geo-cache: CreateContainerConfigError
OK=false
for i in $(seq 1 90); do
  REASON=$(kubectl get pods -n app-273-geocache -l app=geo-cache -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)
  if [ "$REASON" = "CreateContainerConfigError" ]; then OK=true; break; fi
  sleep 2
done
if [ "$OK" = false ]; then
  echo "ERROR: geo-cache never reached CreateContainerConfigError (last: $REASON)"
  exit 1
fi
echo "geo-cache in CreateContainerConfigError"

# img-optimizer: image pull failure
OK=false
for i in $(seq 1 90); do
  REASON=$(kubectl get pods -n app-273-imgopt -l app=img-optimizer -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)
  if [ "$REASON" = "ImagePullBackOff" ] || [ "$REASON" = "ErrImagePull" ]; then OK=true; break; fi
  sleep 2
done
if [ "$OK" = false ]; then
  echo "ERROR: img-optimizer never reached image pull failure (last: $REASON)"
  exit 1
fi
echo "img-optimizer in $REASON"

# auth-svc: crashed at least once with code in logs
OK=false
for i in $(seq 1 150); do
  RESTARTS=$(kubectl get pods -n app-273-auth -l app=auth-svc -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)
  if [ -n "$RESTARTS" ] && [ "$RESTARTS" -ge 1 ] 2>/dev/null; then
    if kubectl logs -n app-273-auth -l app=auth-svc --tail=5 2>/dev/null | grep -q "ERR-AUTH-92c4e1" || \
       kubectl logs -n app-273-auth -l app=auth-svc --tail=5 --previous 2>/dev/null | grep -q "ERR-AUTH-92c4e1"; then
      OK=true; break
    fi
  fi
  sleep 2
done
if [ "$OK" = false ]; then
  echo "ERROR: auth-svc never crashed with code in logs (restarts: $RESTARTS)"
  exit 1
fi
echo "auth-svc crashing with code present"

echo "Setup complete: 24 services (6 broken, 18 healthy)"
