# DBADash-Web Integration Design

**Date:** 2026-03-30
**Status:** Approved
**Scope:** Read-only Python toolset for HolmesGPT to investigate SQL Server issues via dbdash-web APIs

## Overview

Add a `dbdash` Python toolset to HolmesGPT that connects to a dbdash-web instance to investigate database performance issues and triage alerts. The toolset provides 12 read-only tools covering instance discovery, alert triage, performance metrics, and query analysis.

dbdash-web is an enterprise SQL Server monitoring platform (Next.js + SQL Server) deployed at `https://db-monitor.shared.platform.pditechnologies.com`. It monitors multiple SQL Server instances across projects, providing real-time performance dashboards, alert management, and query analysis.

## Goals

- Holmes can answer "why is the database slow?" by pulling real metrics from dbdash-web
- Holmes can triage active alerts by correlating alert data with performance metrics
- Instance scoping via tags ensures Holmes only sees instances belonging to the configured project
- Read-only operations only — no mutations

## Non-Goals

- Writing back to dbdash-web (acknowledging alerts, creating tags, etc.)
- AI analysis endpoint (`/api/ai/analyze`) — Holmes IS the AI analysis
- Settings, configuration, or admin endpoints
- Job monitoring, schema change tracking, HA/DR monitoring (future scope)

## File Structure

```
holmes/plugins/toolsets/dbdash/
├── __init__.py
├── dbdash_toolset.py          # Main Toolset class + prerequisites
├── common.py                  # DBADashConfig (Pydantic) + DBADashClient (HTTP)
├── tools/
│   ├── __init__.py
│   ├── instances.py           # dbdash_list_instances, dbdash_get_instance_details
│   ├── alerts.py              # dbdash_get_active_alerts, dbdash_get_closed_alerts
│   ├── performance.py         # dbdash_get_cpu_metrics, dbdash_get_memory_metrics,
│   │                          # dbdash_get_wait_stats, dbdash_get_io_stats
│   └── queries.py             # dbdash_get_slow_queries, dbdash_get_running_queries,
│                              # dbdash_get_blocking_queries, dbdash_get_query_store_top
└── instructions.jinja2        # LLM investigation workflow guidance
```

## Configuration

### Config Model

```python
class DBADashConfig(ToolsetConfig):
    api_url: str = Field(
        title="DBADash Web URL",
        description="Base URL of the dbdash-web instance",
        examples=["https://db-monitor.shared.platform.pditechnologies.com"],
    )
    username: str = Field(
        title="Username",
        description="Username for JWT authentication",
        examples=["holmes-service"],
    )
    password: str = Field(
        title="Password",
        description="Password for JWT authentication",
        examples=["{{ env.DBDASH_PASSWORD }}"],
    )
    instance_tags: Optional[dict[str, str]] = Field(
        default=None,
        title="Instance Tags",
        description="Filter instances by tags. Only instances matching ALL tags are visible.",
        examples=[{"project": "payments"}],
    )
    verify_ssl: bool = Field(default=True, title="Verify SSL")
    timeout_seconds: int = Field(default=30, title="Request Timeout (seconds)")
```

### User Configuration

```yaml
# ~/.holmes/config.yaml
custom_toolsets:
  dbdash:
    enabled: true
    config:
      api_url: "https://db-monitor.shared.platform.pditechnologies.com"
      username: "holmes-service"
      password: "{{ env.DBDASH_PASSWORD }}"
      instance_tags:
        project: "payments"
```

### Multi-Instance Support

Multiple dbdash-web instances can be configured using the `:suffix` pattern:

```yaml
custom_toolsets:
  dbdash:payments:
    enabled: true
    config:
      api_url: "https://db-monitor.shared.platform.pditechnologies.com"
      username: "holmes-service"
      password: "{{ env.DBDASH_PASSWORD }}"
      instance_tags:
        project: "payments"

  dbdash:logistics:
    enabled: true
    config:
      api_url: "https://db-monitor.shared.platform.pditechnologies.com"
      username: "holmes-service"
      password: "{{ env.DBDASH_PASSWORD }}"
      instance_tags:
        project: "logistics"
```

## Authentication Flow

The `DBADashClient` HTTP wrapper handles JWT authentication transparently:

1. **First API call:** POST `/api/auth/login` with `{username, password}` → server returns JWT as HttpOnly cookie
2. **Token caching:** Store the session cookies in a `requests.Session` object (cookies persist automatically)
3. **Subsequent calls:** The session includes cookies automatically
4. **401 handling:** On any 401 response, re-authenticate and retry the request once
5. **Token lifetime:** JWT is valid for 1 hour; refresh token for 30 days. Re-login on expiry.

### Health Check

`prerequisites_callable` performs:
1. Validate config fields (Pydantic)
2. POST `/api/auth/login` — verify credentials work
3. GET `/api/health` — verify the dbdash-web instance is connected to its database

Returns `(False, "descriptive error")` on any failure.

## Tools

### Discovery Tools

#### `dbdash_list_instances`
- **Endpoint:** GET `/api/instances` + GET `/api/settings/tags`
- **Parameters:** None (tag filtering applied from config)
- **Behavior:**
  1. Fetch all instances from `/api/instances`
  2. Fetch all instance-tag mappings from `/api/settings/tags`
  3. Filter instances to only those matching ALL configured `instance_tags`
  4. Return filtered list with `InstanceID` and `InstanceDisplayName`
- **Returns:** List of `{InstanceID, InstanceDisplayName}` for the configured project

#### `dbdash_get_instance_details`
- **Endpoint:** GET `/api/instances/{instanceId}`
- **Parameters:** `instanceId` (required, integer)
- **Returns:** Instance details including hardware info and collection dates

### Alert Tools

#### `dbdash_get_active_alerts`
- **Endpoint:** GET `/api/alerts?status=active`
- **Parameters:** None
- **Returns:** Active alerts with `{Priority, AlertType, AlertKey, InstanceDisplayName, FirstMessage, TriggerDate, UpdateCount, IsAcknowledged}` + counts `{critical, warning, info, acknowledged}`
- **Note:** Results include alerts from ALL instances. The LLM should cross-reference with the instance list to focus on project-relevant alerts.

#### `dbdash_get_closed_alerts`
- **Endpoint:** GET `/api/alerts?status=closed`
- **Parameters:** None
- **Returns:** Recently closed alerts for historical context during investigation

### Performance Tools

#### `dbdash_get_cpu_metrics`
- **Endpoint:** GET `/api/performance/cpu`
- **Parameters:** `instanceId` (required), `from` (optional, ISO date), `to` (optional, ISO date)
- **Defaults:** Last 24 hours if `from`/`to` not specified
- **Returns:** `{data: [{EventTime, SQLProcessCPU, OtherCPU, MaxCPU}], histogram: [{CPUBucket, OccurrenceCount}]}`

#### `dbdash_get_memory_metrics`
- **Endpoint:** GET `/api/performance/memory`
- **Parameters:** `instanceId` (required), `from` (optional), `to` (optional)
- **Returns:** `{data: [{EventTime, TotalServerMemoryKB, TargetServerMemoryKB, FreeMemoryKB}], clerks: [{ClerkType, SizeKB}]}`

#### `dbdash_get_wait_stats`
- **Endpoint:** GET `/api/performance/waits`
- **Parameters:** `instanceId` (required), `from` (optional), `to` (optional)
- **Returns:** `{data: [{Time, WaitType, IsCriticalWait, TotalWaitSec, ...}], summary: [{WaitType, Description, TotalWaitSec, ...}]}`

#### `dbdash_get_io_stats`
- **Endpoint:** GET `/api/performance/io`
- **Parameters:** `instanceId` (required), `from` (optional), `to` (optional)
- **Returns:** `{timeSeries: [{SnapshotDate, ReadLatency, WriteLatency, ...}], dbSummary: [{DatabaseName, ...}], fgSummary: [...]}`

### Query Tools

#### `dbdash_get_slow_queries`
- **Endpoint:** GET `/api/queries/slow`
- **Parameters:** `instanceId` (optional), `from` (optional), `to` (optional)
- **Returns:** `{summary: [{Grp, LessThan5s, ..., Total, TotalDurationMs, TotalCPUMs}], detail: [{InstanceDisplayName, DatabaseName, ObjectName, Duration, CPU, Reads, SQLText, ...}]}`

#### `dbdash_get_running_queries`
- **Endpoint:** GET `/api/queries/running`
- **Parameters:** `instanceId` (required), `minDuration` (optional, seconds), `blockedOnly` (optional, boolean)
- **Returns:** `{data: [{SPID, DatabaseName, LoginName, Status, Duration, WaitType, BlockedBySPID, SQLText, ...}]}`

#### `dbdash_get_blocking_queries`
- **Endpoint:** GET `/api/queries/blocking`
- **Parameters:** `instanceId` (optional), `from` (optional), `to` (optional)
- **Returns:** `{data: [{HeadBlockerSPID, BlockedSPID, WaitType, Duration, SQLText, ...}], summary: [{WaitType, TotalWaitTime, BlockingCount}], snapshots: [...]}`

#### `dbdash_get_query_store_top`
- **Endpoint:** GET `/api/queries/query-store`
- **Parameters:** `instanceId` (required), `databaseId` (optional), `metric` (optional: cpu|duration|execution_count|memory|logical_io|physical_io, default: cpu), `top` (optional, default: 100), `from` (optional), `to` (optional)
- **Returns:** `{databases: [{DatabaseID, name}], data: [{QueryID, QueryText, ExecutionCount, TotalCPU, AvgCPU, TotalDuration, ...}], metric: string}`

## LLM Instructions

The `instructions.jinja2` template guides Holmes through a structured investigation workflow:

### Investigation Workflow

```
1. DISCOVER: Call dbdash_list_instances to find available SQL Server instances
   - Only instances matching the configured project tags are returned
   - Note instance IDs for subsequent tool calls

2. TRIAGE (if investigating alerts):
   a. Call dbdash_get_active_alerts to see current alerts
   b. Cross-reference alert instances with your instance list
   c. Focus on alerts from project-relevant instances

3. DIAGNOSE (performance investigation):
   a. Start with dbdash_get_cpu_metrics — is CPU high? SQL or external?
   b. Check dbdash_get_wait_stats — what is the primary bottleneck?
      - PAGEIOLATCH_* → check I/O stats
      - LCK_* → check blocking queries
      - RESOURCE_SEMAPHORE → check memory
      - CXPACKET/CXCONSUMER → parallelism issues
   c. Based on wait type, drill into:
      - dbdash_get_io_stats for I/O bottlenecks
      - dbdash_get_memory_metrics for memory pressure
      - dbdash_get_blocking_queries for lock contention
   d. Check dbdash_get_slow_queries for problematic queries
   e. Use dbdash_get_query_store_top to find top resource consumers

4. CORRELATE: Cross-reference findings across tools
   - High CPU + top Query Store queries → identify the culprit query
   - Blocking + slow queries → identify the head blocker
   - Memory pressure + wait stats → confirm memory-related waits
```

### Key Guidance for the LLM

- Always start with instance discovery — never assume instance IDs
- Use time ranges that match the incident window (narrow from 24h default)
- When investigating alerts, check both active and recently closed alerts for patterns
- For running queries, use `blockedOnly=true` when investigating blocking
- Query Store `metric` parameter should match the investigation focus (cpu for CPU issues, duration for slow queries, etc.)

## Error Handling

All tools follow the CLAUDE.md error reporting pattern:

```python
# On HTTP error:
StructuredToolResult(
    status=StructuredToolResultStatus.ERROR,
    error=(
        f"Failed to fetch CPU metrics from {self.toolset.config.api_url}/api/performance/cpu "
        f"with params instanceId={params['instanceId']}, from={params.get('from')}, to={params.get('to')}. "
        f"HTTP {response.status_code}: {response.text}"
    ),
    params=params,
)

# On empty data:
StructuredToolResult(
    status=StructuredToolResultStatus.NO_DATA,
    error=(
        f"No CPU data found for instance ID {params['instanceId']} "
        f"between {params.get('from', 'last 24h')} and {params.get('to', 'now')}. "
        f"The instance may not have recent collection data."
    ),
    params=params,
)
```

## Instance Tag Filtering

Since `/api/instances` doesn't support server-side tag filtering, the toolset implements client-side filtering:

1. `dbdash_list_instances` fetches both `/api/instances` (all instances) and `/api/settings/tags` (all tag mappings)
2. From the tags response, build a map: `instance_id → {tag_name: tag_value}`
3. Filter instances where ALL configured `instance_tags` match
4. Return only matching instances

This runs once per `dbdash_list_instances` call. The filtered instance list is NOT cached between tool calls — the LLM remembers instance IDs from the discovery step.

## Registration

Add to `holmes/plugins/toolsets/__init__.py`:

```python
PYTHON_TOOLSET_FACTORIES: dict[str, type] = {
    # ... existing entries ...
    "dbdash": DBADashToolset,
}
```

## Testing

### Unit Tests
- `tests/test_dbdash_config.py`: Config validation, deprecated field handling
- `tests/test_dbdash_client.py`: JWT auth flow, token refresh, error handling (mocked HTTP)
- `tests/test_dbdash_tag_filtering.py`: Instance filtering by tags with various scenarios

### Integration Test
- `tests/test_dbdash_integration.py`: Against live dbdash-web instance (requires `DBDASH_URL`, `DBDASH_USERNAME`, `DBDASH_PASSWORD` env vars)

### LLM Eval Test
- Scenario: "Why is the database slow?" with mocked dbdash responses showing high CPU + blocking queries
- Verifies Holmes calls the right tools in the right order and produces a coherent diagnosis

## Security

- Credentials stored in environment variables, never in config files
- JWT tokens cached in memory only (not persisted)
- All operations are read-only
- SSL verification enabled by default
- Password field supports `{{ env.DBDASH_PASSWORD }}` template syntax
