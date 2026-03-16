# Infrastructure Setup Patterns

## Verification Principle: Verify the Needle, Not the Haystack

The purpose of setup verification is to confirm that **Holmes can discover the answer** — not that every piece of infrastructure is healthy. The setup script should end with one targeted check that proves the "smoking gun" (the specific value Holmes needs to find) is queryable.

If the needle is findable, the infrastructure is working. Do not add separate checks for pod readiness, service health, API endpoints, etc. — these just bloat the script and make it hard to read.

```bash
# GOOD - one verification that proves Holmes can find the answer
# This implicitly proves: namespace exists, ConfigMap was created, data is correct
VERIFIED=false
for i in $(seq 1 30); do
  if kubectl get configmap platform-config -n app-212 \
    -o jsonpath='{.data.platform-config\.yaml}' 2>/dev/null | grep -q '7k3m9x'; then
    echo "Needle verified on attempt $i"
    VERIFIED=true
    break
  fi
  sleep 1
done
if [ "$VERIFIED" = false ]; then
  echo "Needle not found in ConfigMap"
  exit 1
fi
```

```bash
# BAD - verifying every layer separately
kubectl wait --for=condition=ready pod -l app=grafana ...  # unnecessary
kubectl exec deployment/grafana -- wget -q -O- http://localhost:3000/api/health  # unnecessary
kubectl exec deployment/grafana -- wget -q -O- http://localhost:3000/api/dashboards/...  # unnecessary
# ... then finally checking the actual needle
# All of these intermediate checks add no value — if the needle query works, everything works
```

**When is infrastructure verification appropriate?** Only when the infrastructure itself takes time to initialize (e.g., a service needs to ingest data before it's queryable). In that case, the verification should still target the needle, just with a longer retry loop or `setup_timeout`.

## Idempotent Namespace Creation

```bash
kubectl create namespace app-NNN --dry-run=client -o yaml | kubectl apply -f -
```

## Retry Loops

Never use bare `kubectl wait` immediately after resource creation. Wrap in a retry loop:

```bash
POD_READY=false
for i in $(seq 1 60); do
  if kubectl wait --for=condition=ready pod -l app=myapp -n app-NNN --timeout=5s 2>/dev/null; then
    POD_READY=true
    break
  fi
  sleep 1
done
if [ "$POD_READY" = false ]; then
  echo "Pod failed to become ready"
  exit 1
fi
```

Use `sleep 1` (not `sleep 5`) in retry loops. Question every sleep — many are unnecessary.

## kubectl exec Over Port Forwarding

For any verification inside `before_test`, prefer `kubectl exec` to avoid port conflicts:

```bash
# GOOD - no port conflicts
kubectl exec -n app-NNN deployment/grafana -- \
  wget -q -O- http://localhost:3000/api/health

# AVOID in before_test - port conflicts with other tests
kubectl port-forward svc/grafana 3000:3000 -n app-NNN &
curl localhost:3000/api/health
kill $!
```

## Shared Infrastructure

For common services (Loki, Prometheus, Grafana), use shared manifests:

```bash
kubectl apply -f ../../shared/loki.yaml -n app-NNN
kubectl apply -f ../../shared/prometheus.yaml -n app-NNN
kubectl apply -f ../../shared/grafana.yaml -n app-NNN
```

Shared manifests live in `tests/llm/fixtures/shared/` and provide standardized, version-pinned service configurations.

## Port Forwards

When the test requires accessing a service from within the test toolset config:

**test_case.yaml:**
```yaml
port_forwards:
  - namespace: app-177
    service: grafana
    local_port: 10177    # Convention: 10000 + test_id or unique
    remote_port: 3000
```

**toolsets.yaml:**
```yaml
toolsets:
  grafana/dashboards:
    enabled: true
    config:
      url: http://localhost:10177
      api_key: ""
```

Local port rules:
- Must be unique across ALL tests (not just currently running ones)
- Convention: use `10NNN` or `3NNN` patterns based on test ID
- Port conflicts are auto-detected before setup — conflicting tests are skipped

## Scripts as Kubernetes Secrets

For Python/Bash scripts that run inside pods, use Secrets (not ConfigMaps or inline):

```bash
kubectl create secret generic my-script \
  --from-file=generate_logs.py=./generate_logs.py \
  -n app-NNN --dry-run=client -o yaml | kubectl apply -f -
```

Mount in pod manifest:

```yaml
volumes:
  - name: script
    secret:
      secretName: my-script
      defaultMode: 0755
containers:
  - name: app
    volumeMounts:
      - name: script
        mountPath: /scripts
```

## Container Image Versions

Never use `:latest`. Always pin specific versions:

```yaml
# WRONG
image: grafana/grafana:latest

# CORRECT
image: grafana/grafana:12.3.1
```

## Cloud Service Evals (No Kubernetes)

For tests against cloud services (Elasticsearch, external APIs):

1. Use environment variables in `toolsets.yaml`:
   ```yaml
   toolsets:
     elasticsearch/query:
       enabled: true
       config:
         url: "{{ env.ELASTICSEARCH_URL }}"
         api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
   ```

2. Add URL patterns to `conftest.py` passthrough list (search for `rsps.add_passthru`):
   ```python
   rsps.add_passthru(re.compile(r"https://.*\.cloud\.es\.io"))
   ```

3. Add required secrets to `.github/workflows/eval-regression.yaml`

4. `before_test` creates test data idempotently (see reentrancy below)

### Cloud Service Eval Reentrancy

The same eval can run in parallel across multiple PRs in CI. Kubernetes evals don't have this problem because each PR gets its own KIND cluster, so namespaces are already isolated. Cloud service evals share a single account/instance across all PR runs. This means:

- **`before_test` must be idempotent**: create-or-reuse resources, never fail if they already exist. Handle "already exists" responses from APIs (e.g., HTTP 400/409 with "already exists" in body).
- **`after_test` cleanup depends on naming strategy**:
  - **Test-ID-only names** (e.g., `HLMS233`): NOT safe to delete — the same eval on another PR may be using them. Omit `after_test` entirely.
  - **Run-scoped unique names** (e.g., `HLMS233-${GITHUB_RUN_ID}`): Safe to delete — each run owns its resources. But this requires dynamic names in `user_prompt`, which the test framework doesn't currently support.
  - In practice, most cloud service evals use test-ID-only names and skip cleanup.
- **Use test-ID-based resource names** (e.g., `HLMS233` for Confluence space keys) to avoid collisions between different evals, while accepting that the *same* eval can overlap with itself across parallel PR runs.

```yaml
# GOOD - idempotent setup, no destructive cleanup
before_test: |
  # Create space (or reuse if exists)
  RESPONSE=$(curl -s -w "\n%{http_code}" -X POST .../space ...)
  if [ "$HTTP_CODE" -eq 200 ]; then echo "Created."
  elif echo "$BODY" | grep -q "already exists"; then echo "Reusing."
  else exit 1; fi

# No after_test — resources are tiny, reused across runs

# BAD - deletes resources that a parallel run may be using
after_test: |
  curl -X DELETE .../space/HLMS233  # Another CI run may be mid-test!
```

## Cleanup Pattern

Always handle cleanup gracefully:

```yaml
after_test: |
  kubectl delete namespace app-NNN --ignore-not-found
  rm -f /tmp/holmesgpt-test-NNN-*.yaml
```

Use `--ignore-not-found` to prevent cleanup failures if resources don't exist.

**Note:** This pattern applies to Kubernetes evals where each PR gets its own KIND cluster. For cloud service evals that share a single account across PR runs, see [Cloud Service Eval Reentrancy](#cloud-service-eval-reentrancy) — deleting resources in `after_test` is often unsafe.
