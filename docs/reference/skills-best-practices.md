# Skills Best Practices

This guide helps you create effective, maintainable skills that improve Holmes's ability to troubleshoot issues in your environment. Skills are most valuable when they encode your team's domain knowledge and capture recurring investigation patterns.

## When to Use Skills vs. Team Instructions

The first question when capturing knowledge is: **Does every Holmes session need this?**

If knowledge applies to all investigations:

- Use Team Instructions (stored in `knowledge -> global -> Team Instructions`)
- Examples: your team's naming conventions, company policies, general best practices

If knowledge is situational or optional:

- Use **Skills** instead
- Skills are pulled on-demand, so Holmes only fetches relevant ones based on the investigation context
- Examples: troubleshooting guides for specific services, diagnostic procedures, documented workarounds

**Bottom line**: Team Instructions are always available but consume tokens. Skills are lazy-loaded and more efficient.

## Writing Good Skill Descriptions

Holmes matches skills to investigations using the `description` field. **Make descriptions as specific as possible** — this helps Holmes recognize when a skill applies without false matches that waste tokens.

The `description` field appears in the YAML frontmatter of your `SKILL.md`:

```markdown
---
name: kafka-disk-pressure
description: Diagnose and resolve high disk usage on Kafka brokers caused by segment retention
---
```

**Good descriptions**:

- Specific: "Troubleshoot database connection pool exhaustion in production"
- Action-oriented: "Recover from a split-brain etcd cluster"
- Problem-focused: "Investigate why a Kafka broker isn't rebalancing after node failure"

**Avoid generic descriptions**:

- ❌ "Kafka troubleshooting" — too broad
- ❌ "Common issues" — tells Holmes nothing
- ❌ "Debugging" — matches everything

When Holmes sees a specific description like "database connection pool exhaustion," it knows exactly when to fetch that skill. Generic descriptions cause Holmes to fetch many skills, burning tokens on irrelevant ones.

## Generating Skills from Investigations

At the end of an important investigation that required significant troubleshooting, Holmes can help you generate a skill to capture that knowledge:

```
Holmes, based on what we just investigated, can you create a skill that captures
this troubleshooting process so we don't have to rediscover it next time?
```

Holmes will generate a `SKILL.md` with:
- A clear description matching the issue
- The workflow steps you used
- Expected outputs at each step
- Remediation actions

Review the generated skill, add ownership context and any environment-specific topology, then commit it to your skills repository or add it via our UI.

## Use Skills to Encode Environment Context

One of the highest-value uses for skills is to capture the **topology and dependencies of your systems** so Holmes doesn't have to discover them every time.

For example, instead of Holmes discovering all downstream services on every investigation, encode it:

```markdown
---
name: kafka-broker-topology
description: Kafka broker architecture and downstream service dependencies in production
---

## System Topology

**Kafka Cluster:**
- `kafka-broker-1.prod`, `kafka-broker-2.prod`, `kafka-broker-3.prod` (3-node cluster)
- Partition replication factor: 3
- Min in-sync replicas: 2

**Producer Services:**
- `user-service` → publishes to `user-events` topic
- `order-service` → publishes to `order-events` topic
- `payment-service` → publishes to `payment-events` topic

**Consumer Services:**
- `analytics-pipeline` → consumes from `user-events` and `order-events`
- `fraud-detection` → consumes from `payment-events` and `order-events`
- `notification-service` → consumes from `user-events`

**Failure Patterns:**
When `kafka-broker-2` has high disk usage, it's usually caused by:
1. Consumer group `analytics-pipeline` is behind on lag (common after deployments)
2. Check `analytics-pipeline` deployment status and logs first
3. If healthy, check retention settings on `user-events` and `order-events`
```

This skill saves Holmes from discovering the entire topology by asking Kubernetes for deployments, cross-referencing topic consumers, checking retention policies, etc. Instead, Holmes has immediate context about what services talk to Kafka and known patterns to investigate first.

## Document Known Failure Patterns

If a service has recurring problems, capture the diagnosis in a skill. This is especially valuable for issues that come back regularly or have non-obvious root causes. When an issue comes up during a Holmes investigation, you can ask Holmes to summarize and generate a skill from that investigation so you don't have to rediscover the same pattern in the future.

```markdown
---
name: redis-memory-spike-pattern
description: Diagnose Redis memory spikes from cache key TTL misconfigurations
---

## Known Failure Pattern: Memory Spike Every 6 Hours

**Symptom:**
- Redis memory usage grows steadily, then drops suddenly every 6 hours
- Correlates with `cache-refresh` service redeployments

**Root Cause:**
- `cache-refresh` service doesn't set TTLs on cache keys (or uses very long TTLs)
- Every 6 hours, the service restarts (via pod disruption budget)
- On restart, keys are re-written with proper TTLs
- Without TTLs, keys accumulate until garbage collection runs

**Investigation Workflow:**
1. Check when the spike occurs vs. when `cache-refresh` pod restarts
2. Query Redis with `redis-cli --scan --pattern "*"` to check key count before/after spike
3. Look for keys in `SCAN` output without `EXPIRE` set (check with `TTL <key>`)

**Remediation:**
- Update `cache-refresh` service to set TTL on all keys (see code diff in ticket #456)
- Verify with `redis-cli --bigkeys` after deployment
```

## Add Ownership and Escalation Context

Skills should answer: **Who owns this service, and who should be contacted if the issue persists?**

```markdown
---
name: postgres-connection-pool-exhaustion
description: Recover from PostgreSQL connection pool exhaustion and identify the client
---

## Service Ownership

- **Service Owner**: Backend Platform team (Slack: #backend-platform)
- **On-Call**: Page backend-platform-oncall via PagerDuty
- **Escalation**: Engineering Manager: john@company.com

## Incident Contacts

- Database access issues → Contact the Database Infrastructure team
- Replica lag → Escalate to Database team lead
- Emergency: Page the infrastructure on-call

## Workflow

1. Check current connection count: `SELECT count(*) FROM pg_stat_activity;`
2. Identify clients holding connections: Query `usename` and `application_name`
3. ...rest of investigation steps...
```

This helps Holmes give more actionable recommendations: "High connection count from batch-processor service. This is owned by the Data team. Slack @data-team-oncall or page Data Infrastructure via PagerDuty."

## Keep Skills Up to Date

Outdated skills can mislead investigations. Update or remove skills when:

- **Services are renamed** — old skill names will be discovered but fail
- **Ownership changes** — escalation paths become wrong
- **Dependencies move** — topology in the skill no longer matches production
- **Clusters are replaced** — hostnames, namespaces, or IP ranges change
- **Runbooks change** — steps in the skill become outdated
- **Software versions upgrade** — troubleshooting steps may not apply

Establish a quarterly review process (or tie it to your release cycle):

```bash
# Check for stale skills
find /path/to/skills -name "SKILL.md" -type f -mtime +180 \
  | xargs grep -l "Last Updated:" \
  | head -10
```

Add a "Last Updated" field to skills:

```markdown
---
name: kubernetes-node-drain
description: Safely drain and replace a Kubernetes node
last_updated: 2025-06-30
---
```

When a skill is no longer relevant, delete it rather than leaving it broken. Holmes will perform better with fewer accurate skills than with many stale or broken ones.

## Avoid Secrets in Skills

Skills are sent to the LLM when Holmes decides they are relevant to an investigation. Because of that, skills should never contain information that should remain private or protected.

**Never include secrets, API keys, passwords, tokens, private keys, certificates, kubeconfigs, database credentials, or any other sensitive credentials in a skill.**

A good rule of thumb: if you would not want the LLM to repeat the value back in an answer, do not put it in a skill.

## Limit Skills to Relevant Clusters

In the Robusta UI, you can restrict skills to specific clusters. If a skill is only relevant to your `production-us-east` cluster, don't send it to other clusters — it adds noise to Holmes's decision-making.

**Example**: A skill about troubleshooting an on-prem Elasticsearch cluster only matters if Holmes is investigating issues in a cluster that actually runs Elasticsearch. Remove it from clusters that don't have Elasticsearch.

This reduces the number of skills Holmes considers, making matching faster and more accurate.
