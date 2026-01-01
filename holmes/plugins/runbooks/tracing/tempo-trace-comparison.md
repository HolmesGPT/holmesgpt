# Trace Comparison for Latency and Errors

**When to use this runbook:** Invoke it when users or alerts mention slow requests, rising p95/p99 latency, elevated error rates (4xx/5xx), retry storms, or trace-based performance/regression signals in any supported tracing backend (e.g., Tempo, Datadog, or other OpenTelemetry-compatible systems).

## Goal
- **Primary Objective:** Diagnose latency or error spikes visible in distributed traces by comparing slow/error traces with typical/fast/healthy traces to isolate the driver.
- **Scope:** Applies to services instrumented with distributed tracing in supported backends. Focus on common resource attributes (service, namespace, deployment, pod/node) and span attributes (route, status, database/external calls). Works across tracing providers as long as search and trace-fetch tools are available.
- **Agent Mandate:** Follow the workflow steps in order—collect baseline signals, gather representative traces, compare attributes, and validate findings—without skipping steps.
- **Expected Outcome:** Identify where latency or errors accumulate (service, span, dependency, or infrastructure layer), provide evidence from trace comparisons, and outline remediation or escalation steps.

## Workflow for Trace Performance/Error Diagnosis (optimized for fewer turns)
1. **Establish investigation context**
   - **Action:** Confirm target service/endpoint, namespace, and timeframe from the alert or user input. If unclear, request clarification before proceeding.
   - **Function Description:** Collect context variables and set a reasonable time window (e.g., last 30–60 minutes) for trace and metric queries.
   - **Parameters:** service/endpoint name, namespace, suspected operation, time window.
   - **Expected Output:** Clear scope (service + route + namespace) and time bounds to reuse across queries.
   - **Success/Failure Criteria:** Success = scope defined; Failure = missing context → ask for details.

2. **Get a single analytics view of symptoms**
   - **Action:** Run one tracing analytics/metrics query that returns latency percentiles (p50/p90/p99) **and** error counts/rates by route/service over the window. Prefer queries that also surface top trace IDs for slow/error requests if the backend supports it.
   - **Function Description:** Use a grouped time-series or facet view (service/route/status) to spot the regression and extract candidate attributes (status codes, dependency hosts, pods/nodes) without multiple separate calls.
   - **Parameters:** Service/endpoint filter, optional namespace/deployment/pod filter, time window, and step/interval if supported.
   - **Expected Output:** A single trend/summary showing when latency/errors regressed, which routes/services are affected, and (if available) top offending trace IDs or attributes.
   - **Success/Failure Criteria:** Success = visible trend + candidate attributes/trace IDs; Failure = empty/errored query → simplify filters, widen window, or fall back to sampling traces.

3. **Pull minimal trace cohorts in two searches**
   - **Action:** Retrieve small, targeted cohorts for comparison with at most two searches: (a) slow/error traces, (b) baseline/healthy traces. Avoid per-trace searches.
   - **Function Description:** Use the tracing search tool twice—once with a duration or error filter for the symptom, once for healthy/successful traces with similar filters—to get ordered summaries and IDs. Set strict limits to reduce fetches (e.g., 5 slow/error + 3 baseline).
   - **Parameters:** Service/route filter, namespace/deployment/pod filters, symptom filter (duration > threshold or error status), success filter for baseline, start/end timestamps, and result limits.
   - **Expected Output:** Two small sets of trace summaries (IDs, start times, durations, status) ready for detailed fetch.
   - **Success/Failure Criteria:** Success = at least a couple of traces in each set; Failure = none found → lower threshold, broaden filters, or widen the window.

4. **Fetch trace details in batches**
   - **Action:** Fetch full trace data for the selected IDs **in as few calls as the tool supports** (prefer multi-get/bulk fetch; otherwise fetch only the top 2–3 slow/error and 1–2 baseline traces).
   - **Function Description:** Use trace-by-ID fetch with batching where available to pull spans, attributes, retries, and errors.
   - **Parameters:** Trace IDs from both cohorts, start/end bounds matching earlier queries.
   - **Expected Output:** Span timelines including attributes (db/system, http status/target, messaging peers), durations, retries, and errors/exceptions across both cohorts.
   - **Success/Failure Criteria:** Success = full traces returned for the limited set; Failure = missing data → re-run with exact start/end or confirm retention window.

5. **Compare spans and attributes in one pass**
   - **Action:** Analyze differences between slow/error traces and baseline traces together, focusing on repeated hotspots rather than per-trace narratives.
   - **Function Description:** In a single comparison step, map critical path ordering, longest spans, retries, error tags/status codes, remote endpoints, and resource attributes (node/pod/deployment) across all fetched traces. Identify spans that repeatedly dominate latency or consistently fail.
   - **Parameters:** Span fields from the fetched traces; focus on outliers (long spans, repeated segments, error status, retries/backoff patterns) and any attributes highlighted by analytics.
   - **Expected Output:** Consolidated list of deviations (e.g., specific span/dependency consistently slower or failing, queueing spans added, elevated error codes tied to a pod/node/dependency).
   - **Success/Failure Criteria:** Success = concrete differences identified with shared evidence across traces; Failure = traces look identical → check other endpoints/services or widen the window.

6. **Validate findings with one focused follow-up (optional)**
   - **Action:** Only if needed, run a single focused query on the suspected hotspot attribute (dependency host, span name, pod/node) to confirm prevalence across the window.
   - **Function Description:** Query traces or trace analytics filtered by the suspected attribute to verify it dominates failures/latency.
   - **Parameters:** Attribute filter plus time window and reasonable limit/step.
   - **Expected Output:** Corroborating evidence showing the same latency/error hotspot across multiple traces.
   - **Success/Failure Criteria:** Success = pattern repeats; Failure = inconsistent → reassess candidate causes or broaden scope.

## Synthesize Findings
- **Data Correlation:** Combine latency/error trends with trace comparisons to pinpoint when and where the problem occurs (service/endpoint, span type, dependency, or infrastructure label).
- **Pattern Recognition:** Call out recurring slow spans, repeated retries, elevated error codes, external calls with high duration, or spans tied to specific pods/nodes/deployments.
- **Prioritization Logic:** Rank causes by frequency and impact (e.g., critical path spans that add the most latency or fail most often across samples).
- **Evidence Requirements:** Each suspected cause should cite trace IDs, span names, durations/error status, and relevant attributes (service, endpoint, dependency host, pod/node).
- **Example Synthesis:** “Slow traces for `checkout` (trace IDs …) show `POST /payments` span taking 1.8–2.3s vs. 250ms in baseline, with retries to `payments-api` returning HTTP 429—primary latency driver.”

## Recommended Remediation Steps
- **Immediate Actions:** Reduce load on the hotspot (scale replicas, throttle noisy callers), enable backoff for failing dependencies, and clear retry storms or stuck queues.
- **Permanent Solutions:** Optimize the identified slow/failing span (e.g., fix database query or cache misses), fix downstream errors, adjust client/server timeouts/backoff, and right-size resources for the affected service or node pool.
- **Verification Steps:** Re-run tracing analytics/searches after changes to confirm latency/error improvements and disappearance of the problematic span pattern.
- **Documentation References:** Consult tracing backend docs (e.g., Tempo, Datadog APM) plus service-specific performance/error runbooks and dependency owner docs.
- **Escalation Criteria:** Escalate if the issue is driven by a third-party/shared dependency outside ownership, or if no actionable differences emerge after multiple trace comparisons.
- **Post-Remediation Monitoring:** Keep latency percentiles and error rates for the affected endpoints under watch; create alerts on p95/p99 regressions, retries, and error surges targeting the identified dependency.
